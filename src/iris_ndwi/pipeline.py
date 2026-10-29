"""End-to-end run: search -> select -> read -> classify -> summarise."""

from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from shapely.ops import unary_union

from iris_ndwi.config import PipelineConfig
from iris_ndwi.ndwi import METHOD_VERSION, below_offset_fraction, classify_pixels
from iris_ndwi.parcels import Parcel, reproject
from iris_ndwi.scenes import Scene, SceneRejection, dominant_tile, select_scenes
from iris_ndwi.sources import SceneSource, SourceError
from iris_ndwi.zonal import ParcelObservation, parcel_pixel_mask, summarize

log = logging.getLogger(__name__)

# Read margin around the parcels, in metres (scene CRS).
READ_BUFFER_M = 20

# Config keys that change an observation's values or its acceptance. The period,
# collections and tile only decide which scenes are read; the scene is in the key anyway.
METHOD_PARAMS = (
    "target_crs",
    "valid_scl_classes",
    "max_blue_reflectance",
    "require_positive_reflectance",
    "max_below_offset_fraction",
    "min_valid_fraction",
    "water_threshold",
)


class SceneQualityError(RuntimeError):
    """A scene that was read but fails a scene-level quality check."""


@dataclass
class RunResult:
    run_id: str
    started_at: datetime
    finished_at: datetime | None
    config: PipelineConfig
    config_sha256: str
    method_params_sha256: str
    source_name: str
    parcels: list[Parcel]
    n_items_found: int = 0
    scenes: list[Scene] = field(default_factory=list)
    scene_rejections: list[SceneRejection] = field(default_factory=list)
    observations: list[ParcelObservation] = field(default_factory=list)


def config_digest(config: PipelineConfig) -> str:
    # an empty `areas` (added later) is left out, so earlier configurations keep their digest and run_id
    payload = json.dumps(config.model_dump(mode="json", exclude=set() if config.areas else {"areas"}), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def method_params_digest(config: dict) -> str:
    """SHA-256 of the method thresholds; takes config.model_dump(mode="json") or a manifest's config."""
    payload = json.dumps({key: config.get(key) for key in METHOD_PARAMS}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def process_scene(
    source: SceneSource, scene: Scene, parcels: list[Parcel], config: PipelineConfig, params_digest: str
) -> list[ParcelObservation]:
    """Read one scene once for all parcels and summarise each parcel."""
    in_scene_crs = {p.parcel_id: reproject(p.geometry, p.crs, scene.crs) for p in parcels}
    bounds = unary_union(list(in_scene_crs.values())).buffer(READ_BUFFER_M).bounds
    window = source.read(scene, bounds)

    below = below_offset_fraction(
        window.green_dn, window.nir_dn, window.scl, scene.green, scene.nir, config.valid_scl_classes
    )
    if below is not None and below > config.max_below_offset_fraction:
        raise SceneQualityError(
            f"radiometric_offset_inconsistent: {below:.1%} of clear pixels below the advertised "
            f"BOA offset ({scene.green.offset:+g})"
        )

    values, status = classify_pixels(
        window.blue_dn,
        window.green_dn,
        window.nir_dn,
        window.scl,
        scene.blue,
        scene.green,
        scene.nir,
        scene.scl.nodata,
        config.valid_scl_classes,
        config.require_positive_reflectance,
        config.max_blue_reflectance,
    )
    return [
        summarize(
            parcel_id=parcel.parcel_id,
            item_id=scene.item_id,
            acquired_at=scene.acquired_at,
            method_version=METHOD_VERSION,
            method_params_sha256=params_digest,
            inside=parcel_pixel_mask(in_scene_crs[parcel.parcel_id], values.shape, window.transform),
            values=values,
            status=status,
            min_valid_fraction=config.min_valid_fraction,
            water_threshold=config.water_threshold,
        )
        for parcel in parcels
    ]


def run_pipeline(config: PipelineConfig, source: SceneSource, parcels: list[Parcel]) -> RunResult:
    started_at = datetime.now(timezone.utc)
    aoi_wgs84 = reproject(unary_union([p.geometry for p in parcels]), config.target_crs, "EPSG:4326").envelope
    items = source.search(aoi_wgs84, config.start_date.isoformat(), config.end_date.isoformat())
    if config.mgrs_tile is None:
        tile = dominant_tile(items, aoi_wgs84)
        if tile is None and items:
            raise ValueError(
                "no single Sentinel-2 tile (about 110 km × 110 km) covers the whole area; "
                "choose a smaller area or move it away from the tile edge"
            )
        log.info("mgrs_tile not configured, using %s", tile)
        config = config.model_copy(update={"mgrs_tile": tile})  # recorded in the manifest

    digest = config_digest(config)
    params_digest = method_params_digest(config.model_dump(mode="json"))
    result = RunResult(
        run_id=f"{started_at:%Y%m%dT%H%M%SZ}-{digest[:8]}",
        started_at=started_at,
        finished_at=None,
        config=config,
        config_sha256=digest,
        method_params_sha256=params_digest,
        source_name=source.name,
        parcels=parcels,
        n_items_found=len(items),
    )
    selected, rejected = select_scenes(
        items, config.mgrs_tile, config.max_scene_cloud_pct, aoi_wgs84, config.collections
    )
    log.info("%d items found, %d selected, %d rejected before reading", len(items), len(selected), len(rejected))

    def work(scene: Scene) -> tuple[Scene, list[ParcelObservation] | None, str | None]:
        try:
            return scene, process_scene(source, scene, parcels, config, params_digest), None
        except SourceError as exc:
            log.warning("read failed: %s", exc)
            return scene, None, f"read_error: {exc}"
        except SceneQualityError as exc:
            log.warning("%s: %s", scene.item_id, exc)
            return scene, None, str(exc)
        except Exception as exc:  # one bad scene must not lose a multi-year run; recorded in the ledger
            log.exception("%s: unexpected error", scene.item_id)
            return scene, None, f"processing_error: {type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=config.max_workers) as pool:
        outcomes = list(pool.map(work, selected))

    for scene, observations, error in outcomes:  # pool.map keeps input order
        if error:
            rejected.append(SceneRejection(scene.item_id, scene.acquired_at, error, scene))
            continue
        result.scenes.append(scene)
        result.observations.extend(observations)

    result.scene_rejections = sorted(rejected, key=lambda r: (r.acquired_at is None, r.acquired_at, r.item_id))
    result.finished_at = datetime.now(timezone.utc)
    return result
