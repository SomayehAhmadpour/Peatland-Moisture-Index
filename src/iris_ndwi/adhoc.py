"""Ad-hoc runs started from the dashboard.

- a square box around any point: it inherits every quality setting from a
  base configuration; only the parcel, the dates, the metric CRS and the
  MGRS tile change;
- an earlier run repeated with new dates (and, for a single-box run, a new
  box size): its manifest holds the complete configuration, so everything
  else stays identical.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from iris_ndwi.config import BoxParcel, PipelineConfig
from iris_ndwi.outputs import write_outputs
from iris_ndwi.parcels import load_parcels
from iris_ndwi.pipeline import RunResult, run_pipeline
from iris_ndwi.report import quality_summary
from iris_ndwi.sources import SceneSource, StacSource


def metric_crs(lat: float, lon: float) -> str:
    """EPSG:25832 inside UTM zone 32 (the project CRS), else the point's own UTM zone.

    Outside its zone EPSG:25832 distorts distances, so a 100 m box would no
    longer be 100 m. ETRS89 / UTM (258xx) is used across Europe, WGS 84 / UTM
    (326xx north, 327xx south) elsewhere.
    """
    zone = min(int((lon + 180) // 6) + 1, 60)
    if 34 <= lat <= 72 and 28 <= zone <= 38:
        return f"EPSG:258{zone:02d}"
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


def box_parcel_id(lat: float, lon: float, size_m: float) -> str:
    """Unique per location and size, so boxes of different runs never share a parcel_id in PostGIS."""
    ns, ew = "n" if lat >= 0 else "s", "e" if lon >= 0 else "w"
    text = f"point-{abs(lat):.5f}{ns}-{abs(lon):.5f}{ew}-{size_m:g}m"  # e.g. point-47p92624n-9p87749e-100m
    return text.replace(".", "p")


def point_config(
    base: PipelineConfig,
    lat: float,
    lon: float,
    size_m: float,
    start: date,
    end: date,
    name: str,
) -> PipelineConfig:
    box = BoxParcel(
        parcel_id=box_parcel_id(lat, lon, size_m),
        name=name,
        kind="point",
        center_lat=lat,
        center_lon=lon,
        size_m=size_m,
        snap_m=10,
    )
    return PipelineConfig.model_validate({
        **base.model_dump(),
        "study_area": f"{name} ({lat:.5f}, {lon:.5f})",
        "mgrs_tile": None,
        "start_date": start,
        "end_date": end,
        "target_crs": metric_crs(lat, lon),
        "parcels_geojson": None,
        "boxes": [box.model_dump()],
    })  # fmt: skip


def is_single_box(manifest_config: dict[str, Any]) -> bool:
    """A run of one box around a point (e.g. from point_config), whose size can be changed."""
    return not manifest_config.get("parcels_geojson") and len(manifest_config.get("boxes") or []) == 1


def rerun_config(
    manifest_config: dict[str, Any], start: date, end: date, root: Path, size_m: float | None = None
) -> PipelineConfig:
    """Configuration of an earlier run (from its manifest) with new dates.

    size_m resizes the box of a single-box run around the same centre. A
    relative parcels_geojson path is resolved against root (the repository).
    """
    updates: dict[str, Any] = {"start_date": start, "end_date": end}
    if size_m is not None:
        if not is_single_box(manifest_config):
            raise ValueError("the box size can only be changed for a run with exactly one box")
        box = {**manifest_config["boxes"][0], "size_m": size_m}
        if box.get("kind") == "point":  # a new size is a new parcel
            box["parcel_id"] = box_parcel_id(box["center_lat"], box["center_lon"], size_m)
        updates["boxes"] = [box]
    config = PipelineConfig.model_validate({**manifest_config, **updates})
    if config.parcels_geojson and not config.parcels_geojson.is_absolute():
        config = config.model_copy(update={"parcels_geojson": root / config.parcels_geojson})
    return config


def run_and_write(config: PipelineConfig, output_dir: Path, source: SceneSource | None = None) -> tuple[RunResult, Path]:
    source = source or StacSource(config.stac_url, config.collections)
    result = run_pipeline(config, source, load_parcels(config))
    run_dir = write_outputs(result, output_dir)
    (run_dir / "quality_summary.md").write_text(quality_summary(result))
    return result, run_dir
