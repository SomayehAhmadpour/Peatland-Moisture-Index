"""File outputs: observations, scene ledger, parcels and a provenance manifest."""

from __future__ import annotations

import csv
import json
import platform
from dataclasses import fields
from importlib import metadata
from pathlib import Path

from shapely.geometry import mapping

from iris_ndwi.config import SCL_CLASSES
from iris_ndwi.ndwi import METHOD_VERSION
from iris_ndwi.pipeline import RunResult
from iris_ndwi.zonal import ParcelObservation

SCENE_COLUMNS = [
    "item_id", "collection", "acquired_at", "platform", "mgrs_tile", "processing_baseline", "generation_time",
    "scene_cloud_pct", "boa_offset_applied_flag", "boa_offset", "reflectance_scale", "sun_elevation", "selected",
    "rejection_reason", "green_href", "nir_href", "scl_href",
]  # fmt: skip


def scene_rows(result: RunResult) -> list[dict]:
    rows = [
        {
            "item_id": s.item_id,
            "collection": s.collection,
            "acquired_at": s.acquired_at.isoformat(),
            "platform": s.platform,
            "mgrs_tile": s.mgrs_tile,
            "processing_baseline": s.processing_baseline,
            "generation_time": s.generation_time,
            "scene_cloud_pct": round(s.scene_cloud_pct, 4),
            "boa_offset_applied_flag": s.boa_offset_applied_flag,
            "boa_offset": s.green.offset,
            "reflectance_scale": s.green.scale,
            "sun_elevation": s.sun_elevation,
            "selected": True,
            "rejection_reason": None,
            "green_href": s.green.href,
            "nir_href": s.nir.href,
            "scl_href": s.scl.href,
        }
        for s in result.scenes
    ]
    rows += [
        {"item_id": r.item_id, "acquired_at": r.acquired_at.isoformat() if r.acquired_at else None,
         "selected": False, "rejection_reason": r.reason}
        for r in result.scene_rejections
    ]  # fmt: skip
    return sorted(rows, key=lambda row: (row["acquired_at"] or "", row["item_id"]))


def observation_rows(result: RunResult) -> list[dict]:
    rows = []
    for obs in sorted(result.observations, key=lambda o: (o.parcel_id, o.acquired_at, o.item_id)):
        row = obs.as_row()
        row["acquired_at"] = obs.acquired_at.isoformat()
        rows.append(row)
    return rows


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parcels_geojson(result: RunResult) -> dict:
    epsg = result.config.target_crs.split(":")[1]
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": f"urn:ogc:def:crs:EPSG::{epsg}"}},
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "parcel_id": p.parcel_id, "name": p.name, "kind": p.kind, "source": p.source,
                    "source_ref": p.source_ref, "licence": p.licence, "area_m2": round(p.area_m2, 1),
                    "geometry_repaired": p.geometry_repaired,
                },  # fmt: skip
                "geometry": mapping(p.geometry),
            }
            for p in result.parcels
        ],
    }


def manifest(result: RunResult) -> dict:
    packages = ["numpy", "rasterio", "shapely", "pyproj", "pystac-client"]
    return {
        "run_id": result.run_id,
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat() if result.finished_at else None,
        "method_version": METHOD_VERSION,
        "source": result.source_name,
        "synthetic_data": result.source_name == "synthetic",
        "stac_url": result.config.stac_url,
        "collections": result.config.collections,
        "config": result.config.model_dump(mode="json"),
        "config_sha256": result.config_sha256,
        "valid_scl_classes": {c: SCL_CLASSES[c] for c in result.config.valid_scl_classes},
        "counts": {
            "items_found": result.n_items_found,
            "scenes_processed": len(result.scenes),
            "scenes_rejected": len(result.scene_rejections),
            "observations": len(result.observations),
            "observations_accepted": sum(o.status == "accepted" for o in result.observations),
        },
        "licences": {
            "imagery": "Copernicus Sentinel data, free and open (Sentinel data legal notice); "
            "contains modified Copernicus Sentinel data",
            "parcels": sorted({p.licence for p in result.parcels}),
        },
        "software": {
            "python": platform.python_version(),
            **{name: metadata.version(name) for name in packages},
        },
    }


def write_outputs(result: RunResult, output_dir: Path) -> Path:
    run_dir = Path(output_dir) / result.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(run_dir / "observations.csv", [f.name for f in fields(ParcelObservation)], observation_rows(result))
    _write_csv(run_dir / "scenes.csv", SCENE_COLUMNS, scene_rows(result))
    (run_dir / "parcels.geojson").write_text(json.dumps(parcels_geojson(result), indent=1))
    (run_dir / "manifest.json").write_text(json.dumps(manifest(result), indent=2, default=str))
    return run_dir
