"""Sentinel-2 L2A scene metadata parsed from STAC items, and scene selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

# STAC asset keys used by Earth Search v1 (sentinel-2-l2a and sentinel-2-c1-l2a).
BLUE, GREEN, NIR, SCL = "blue", "green", "nir", "scl"


class SceneMetadataError(ValueError):
    """A STAC item that lacks metadata the method depends on."""


@dataclass(frozen=True)
class BandAsset:
    href: str
    scale: float
    offset: float
    nodata: int


@dataclass(frozen=True)
class Scene:
    item_id: str
    collection: str
    acquired_at: datetime
    platform: str
    mgrs_tile: str
    processing_baseline: str
    generation_time: str
    scene_cloud_pct: float
    tile_nodata_pct: float
    crs: str
    sun_elevation: float | None
    footprint: BaseGeometry | None  # WGS84 item geometry
    blue: BandAsset
    green: BandAsset
    nir: BandAsset
    scl: BandAsset
    boa_offset_applied_flag: bool | None = None  # legacy Earth Search flag, as published

    @property
    def baseline_key(self) -> tuple[int, ...]:
        return tuple(int(part) for part in self.processing_baseline.split("."))


def _band(item: dict, key: str, default_scale: float | None, offset_already_applied: bool = False) -> BandAsset:
    try:
        asset = item["assets"][key]
    except KeyError as exc:
        raise SceneMetadataError(f"{item.get('id')}: missing asset '{key}'") from exc
    bands = asset.get("raster:bands") or [{}]
    meta = bands[0]
    scale = meta.get("scale", default_scale)
    if scale is None:
        raise SceneMetadataError(f"{item['id']}: asset '{key}' has no raster:bands scale")
    return BandAsset(
        href=asset["href"],
        scale=float(scale),
        # Absent offset means no BOA offset (processing baseline < 04.00).
        # Earth Search's legacy collection shifts DN back for items flagged
        # earthsearch:boa_offset_applied=true but keeps advertising -0.1;
        # verified on 2021-05-31 (03.00 vs reprocessed 05.00: DN 474 vs 483).
        offset=0.0 if offset_already_applied else float(meta.get("offset", 0.0)),
        nodata=int(meta.get("nodata", 0)),
    )


def _crs(props: dict) -> str | None:
    if props.get("proj:code"):
        return props["proj:code"]
    if props.get("proj:epsg"):
        return f"EPSG:{props['proj:epsg']}"
    return None


def parse_item(item: dict) -> Scene:
    """Build a Scene from a STAC item dict (Earth Search v1 layout)."""
    props = item.get("properties", {})
    required = ["datetime", "platform", "s2:processing_baseline", "eo:cloud_cover"]
    missing = [key for key in required if props.get(key) is None]
    crs = _crs(props)
    if crs is None:
        missing.append("proj:code|proj:epsg")
    if missing:
        raise SceneMetadataError(f"{item.get('id')}: missing properties {missing}")
    tile = props.get("grid:code", "").removeprefix("MGRS-") or (
        f"{props.get('mgrs:utm_zone')}{props.get('mgrs:latitude_band')}{props.get('mgrs:grid_square')}"
    )
    return Scene(
        item_id=item["id"],
        collection=item.get("collection", ""),
        acquired_at=datetime.fromisoformat(props["datetime"].replace("Z", "+00:00")),
        platform=props["platform"],
        mgrs_tile=tile,
        processing_baseline=props["s2:processing_baseline"],
        generation_time=props.get("s2:generation_time") or props.get("created") or "",
        scene_cloud_pct=float(props["eo:cloud_cover"]),
        tile_nodata_pct=float(props.get("s2:nodata_pixel_percentage") or 0.0),
        crs=crs,
        sun_elevation=props.get("view:sun_elevation"),
        footprint=shape(item["geometry"]) if item.get("geometry") else None,
        blue=_band(item, BLUE, None, props.get("earthsearch:boa_offset_applied") is True),
        green=_band(item, GREEN, None, props.get("earthsearch:boa_offset_applied") is True),
        nir=_band(item, NIR, None, props.get("earthsearch:boa_offset_applied") is True),
        scl=_band(item, SCL, 1.0),
        boa_offset_applied_flag=props.get("earthsearch:boa_offset_applied"),
    )


@dataclass(frozen=True)
class SceneRejection:
    item_id: str
    acquired_at: datetime | None
    reason: str


def aoi_coverage(scene: Scene, aoi_wgs84: BaseGeometry | None) -> float:
    """Share of the AOI inside the item footprint (1.0 when unknown)."""
    if aoi_wgs84 is None or scene.footprint is None or aoi_wgs84.area == 0:
        return 1.0
    return round(scene.footprint.intersection(aoi_wgs84).area / aoi_wgs84.area, 4)


def dominant_tile(items: list[dict], aoi_wgs84: BaseGeometry | None) -> str | None:
    """MGRS tile that fully covers the AOI in the most items (ties: lowest code).

    Neighbouring tiles overlap by ~10 km, so a point can lie in several; one
    tile keeps the pixel grid and CRS identical through the time series.
    """
    counts: dict[str, int] = {}
    for item in items:
        try:
            scene = parse_item(item)
        except SceneMetadataError:
            continue
        if aoi_coverage(scene, aoi_wgs84) >= 0.999:
            counts[scene.mgrs_tile] = counts.get(scene.mgrs_tile, 0) + 1
    return min(counts, key=lambda tile: (-counts[tile], tile)) if counts else None


def select_scenes(
    items: list[dict],
    mgrs_tile: str | None,
    max_scene_cloud_pct: float,
    aoi_wgs84: BaseGeometry | None = None,
    collections: list[str] | None = None,
) -> tuple[list[Scene], list[SceneRejection]]:
    """Parse, filter and de-duplicate STAC items.

    Order of rules (first match wins, each rejection is recorded):
      1. unparseable metadata        -> metadata_error
      2. other MGRS tile             -> other_mgrs_tile
      3. same tile + platform + UTC day seen more than once. Two real causes:
         ESA reprocessing (same pixels, newer baseline) and one pass split
         over two datastrips (each product covers part of the tile); with
         several collections also the same acquisition in each. Keep the item
         with the largest AOI coverage, then the earliest collection in
         `collections` (priority order), then the highest processing
         baseline, then the lowest tile nodata %, then the latest generation
         time                          -> superseded_by:<item_id>
      4. item cloud cover above max   -> scene_cloud_cover_above_max
    """
    rejected: list[SceneRejection] = []
    candidates: list[Scene] = []
    for item in items:
        try:
            scene = parse_item(item)
        except SceneMetadataError as exc:
            rejected.append(SceneRejection(item.get("id", "?"), None, f"metadata_error: {exc}"))
            continue
        if scene.mgrs_tile != mgrs_tile:
            rejected.append(SceneRejection(scene.item_id, scene.acquired_at, "other_mgrs_tile"))
            continue
        candidates.append(scene)

    groups: dict[tuple[str, str, object], list[Scene]] = {}
    for scene in candidates:
        # Products of one acquisition can differ by milliseconds or seconds
        # in datetime, so exact datetimes are not a key. One platform images a
        # given tile at most once per UTC day.
        key = (scene.mgrs_tile, scene.platform, scene.acquired_at.date())
        groups.setdefault(key, []).append(scene)

    priority = {name: rank for rank, name in enumerate(collections or [])}

    selected: list[Scene] = []
    for group in groups.values():
        group.sort(
            key=lambda s: (
                aoi_coverage(s, aoi_wgs84),
                -priority.get(s.collection, len(priority)),
                s.baseline_key,
                -s.tile_nodata_pct,
                s.generation_time,
                s.item_id,
            ),
            reverse=True,
        )
        winner, *superseded = group
        rejected += [
            SceneRejection(s.item_id, s.acquired_at, f"superseded_by:{winner.item_id}") for s in superseded
        ]
        if winner.scene_cloud_pct > max_scene_cloud_pct:
            rejected.append(SceneRejection(winner.item_id, winner.acquired_at, "scene_cloud_cover_above_max"))
        else:
            selected.append(winner)

    selected.sort(key=lambda s: (s.acquired_at, s.item_id))
    rejected.sort(key=lambda r: (r.acquired_at or datetime.min.replace(tzinfo=timezone.utc), r.item_id))
    return selected, rejected
