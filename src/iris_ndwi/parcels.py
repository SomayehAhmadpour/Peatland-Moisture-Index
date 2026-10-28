"""Parcel geometries: loading, validation and reprojection.

All parcels are held in the metric target CRS (EPSG:25832). They are only
reprojected into the scene CRS (EPSG:326xx) at the moment of rasterisation,
so reflectances are never resampled.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon, box, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from iris_ndwi.config import BoxParcel, PipelineConfig


class GeometryError(ValueError):
    """A parcel geometry that cannot be used."""


@dataclass(frozen=True)
class Parcel:
    parcel_id: str
    name: str
    kind: str
    source: str
    source_ref: str
    licence: str
    geometry: MultiPolygon  # in target CRS
    crs: str
    geometry_repaired: bool = False

    @property
    def area_m2(self) -> float:
        return self.geometry.area


@lru_cache(maxsize=16)
def _transformer(src: str, dst: str) -> Transformer:
    return Transformer.from_crs(src, dst, always_xy=True)


def reproject(geometry: BaseGeometry, src: str, dst: str) -> BaseGeometry:
    if src == dst:
        return geometry
    return transform(_transformer(src, dst).transform, geometry)


def validate_geometry(geometry: BaseGeometry, parcel_id: str) -> tuple[MultiPolygon, bool]:
    """Return a valid MultiPolygon and whether it had to be repaired.

    Raises GeometryError for empty, non-areal or zero-area input.
    """
    if geometry is None or geometry.is_empty:
        raise GeometryError(f"{parcel_id}: geometry is empty")
    repaired = False
    if not geometry.is_valid:
        geometry = make_valid(geometry)
        repaired = True
    polygons: list[Polygon] = []
    for part in getattr(geometry, "geoms", [geometry]):
        if isinstance(part, Polygon):
            polygons.append(part)
        elif isinstance(part, MultiPolygon):
            polygons.extend(part.geoms)
    if not polygons:
        raise GeometryError(f"{parcel_id}: geometry has no polygonal part ({geometry.geom_type})")
    result = MultiPolygon(polygons)
    if result.area <= 0:
        raise GeometryError(f"{parcel_id}: geometry has zero area")
    return result, repaired


def square_box(center_lat: float, center_lon: float, size_m: float, snap_m: float | None, crs: str) -> Polygon:
    """Square of edge size_m around a WGS84 point, built in the metric CRS.

    With snap_m the lower-left corner is snapped to a multiple of snap_m so a
    10 m snap aligns the box with the Sentinel-2 10 m pixel grid.
    """
    x, y = _transformer("EPSG:4326", crs).transform(center_lon, center_lat)
    min_x, min_y = x - size_m / 2, y - size_m / 2
    if snap_m:
        min_x = math.floor(min_x / snap_m + 0.5) * snap_m
        min_y = math.floor(min_y / snap_m + 0.5) * snap_m
    return box(min_x, min_y, min_x + size_m, min_y + size_m)


def box_parcel(spec: BoxParcel, crs: str) -> Parcel:
    geometry, repaired = validate_geometry(
        square_box(spec.center_lat, spec.center_lon, spec.size_m, spec.snap_m, crs), spec.parcel_id
    )
    return Parcel(
        parcel_id=spec.parcel_id,
        name=spec.name,
        kind=spec.kind,
        source="config",
        source_ref=f"box:{spec.center_lat:.6f},{spec.center_lon:.6f};{spec.size_m:g}m",
        licence="n/a (derived from configured coordinates)",
        geometry=geometry,
        crs=crs,
        geometry_repaired=repaired,
    )


def load_geojson_parcels(path: Path, crs: str) -> list[Parcel]:
    """Load parcels from a GeoJSON FeatureCollection in EPSG:4326."""
    collection = json.loads(Path(path).read_text())
    parcels = []
    for feature in collection["features"]:
        props = feature["properties"]
        parcel_id = props["parcel_id"]
        geometry, repaired = validate_geometry(
            reproject(shape(feature["geometry"]), "EPSG:4326", crs), parcel_id
        )
        parcels.append(
            Parcel(
                parcel_id=parcel_id,
                name=props["name"],
                kind=props["kind"],
                source=props["source"],
                source_ref=props["source_ref"],
                licence=props["licence"],
                geometry=geometry,
                crs=crs,
                geometry_repaired=repaired,
            )
        )
    return parcels


def load_parcels(config: PipelineConfig) -> list[Parcel]:
    parcels = []
    if config.parcels_geojson:
        parcels += load_geojson_parcels(config.parcels_geojson, config.target_crs)
    parcels += [box_parcel(spec, config.target_crs) for spec in config.boxes]
    ids = [p.parcel_id for p in parcels]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise GeometryError(f"duplicate parcel_id(s): {duplicates}")
    return sorted(parcels, key=lambda p: p.parcel_id)
