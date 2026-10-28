"""Ad-hoc runs for one square box around any point (used by the dashboard).

The box inherits every quality setting from a base configuration; only the
parcel, the dates, the metric CRS and the MGRS tile change.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

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
        parcel_id="point-box",
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


def run_point(config: PipelineConfig, output_dir: Path, source: SceneSource | None = None) -> tuple[RunResult, Path]:
    source = source or StacSource(config.stac_url, config.collections)
    result = run_pipeline(config, source, load_parcels(config))
    run_dir = write_outputs(result, output_dir)
    (run_dir / "quality_summary.md").write_text(quality_summary(result))
    return result, run_dir
