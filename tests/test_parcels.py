import numpy as np
import pytest
from affine import Affine
from conftest import ROOT
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box

from iris_ndwi.config import BoxParcel
from iris_ndwi.parcels import (
    GeometryError,
    box_parcel,
    load_geojson_parcels,
    load_parcels,
    reproject,
    square_box,
    validate_geometry,
)
from iris_ndwi.sources import aligned_windows
from iris_ndwi.zonal import parcel_pixel_mask

TILE_32UNU = Affine(10, 0, 499980, 0, -10, 5400000)


def test_box_is_100m_square_in_metric_crs():
    polygon = square_box(47.926242, 9.877493, 100, 10, "EPSG:25832")
    min_x, min_y, max_x, max_y = polygon.bounds
    assert (max_x - min_x, max_y - min_y) == (100, 100)
    assert min_x % 10 == 0 and min_y % 10 == 0  # snapped to the 10 m grid


def test_unsnapped_box_is_centred_on_point():
    polygon = square_box(47.926242, 9.877493, 100, None, "EPSG:25832")
    center = reproject(Point(9.877493, 47.926242), "EPSG:4326", "EPSG:25832")
    assert polygon.centroid.distance(center) < 1e-6


@pytest.mark.parametrize("lat, lon", [(47.926242, 9.877493), (47.933030, 9.842128), (47.863813, 10.211675)])
def test_snapped_box_covers_exactly_100_sentinel2_pixels(lat, lon):
    """EPSG:25832 box -> EPSG:32632 scene grid still gives 10 x 10 pixel centres."""
    parcel = box_parcel(
        BoxParcel(parcel_id="t-box", name="t", kind="t", center_lat=lat, center_lon=lon, size_m=100), "EPSG:25832"
    )
    in_scene = reproject(parcel.geometry, "EPSG:25832", "EPSG:32632")
    _, window_20, transform = aligned_windows(TILE_32UNU, in_scene.buffer(20).bounds)
    shape = (int(window_20.height) * 2, int(window_20.width) * 2)
    assert np.count_nonzero(parcel_pixel_mask(in_scene, shape, transform)) == 100


def test_self_intersecting_polygon_is_repaired():
    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10), (0, 0)])
    assert not bowtie.is_valid
    geometry, repaired = validate_geometry(bowtie, "bowtie")
    assert repaired and geometry.is_valid and isinstance(geometry, MultiPolygon)
    assert geometry.area == pytest.approx(50)


@pytest.mark.parametrize(
    "geometry, message",
    [
        (Polygon(), "empty"),
        (LineString([(0, 0), (1, 1)]), "no polygonal part"),
        (Point(0, 0), "no polygonal part"),
    ],
)
def test_unusable_geometries_raise(geometry, message):
    with pytest.raises(GeometryError, match=message):
        validate_geometry(geometry, "bad")


def test_zero_area_polygon_is_rejected():
    flat = Polygon([(0, 0), (10, 0), (20, 0), (0, 0)])
    with pytest.raises(GeometryError):
        validate_geometry(flat, "flat")


def test_committed_osm_parcels_load_valid_with_holes():
    parcels = {p.parcel_id: p for p in load_geojson_parcels(ROOT / "data/parcels/wurzacher_ried_osm.geojson", "EPSG:25832")}
    assert set(parcels) == {"wr-bog-osm", "wr-nsg-4035"}
    bog, reserve = parcels["wr-bog-osm"], parcels["wr-nsg-4035"]
    assert bog.geometry.is_valid and reserve.geometry.is_valid
    assert sum(len(p.interiors) for p in bog.geometry.geoms) > 0  # multipolygon holes survived
    assert 1000e4 < bog.area_m2 < reserve.area_m2 < 2000e4  # bog (~1166 ha) inside NSG (~1799 ha)
    # The two OSM features are mapped independently: ~99 % of the bog lies in the
    # NSG, ~12 ha in three slivers do not (documented in QUALITY_REPORT.md).
    assert bog.geometry.intersection(reserve.geometry).area / bog.area_m2 > 0.98


def test_config_parcels_and_boxes_are_inside_reserve(config):
    parcels = {p.parcel_id: p for p in load_parcels(config)}
    reserve = parcels["wr-nsg-4035"].geometry
    assert reserve.contains(parcels["wr-core-100m"].geometry)
    assert not reserve.intersects(parcels["ref-field-100m"].geometry)


def test_duplicate_parcel_ids_rejected(config):
    duplicate = config.boxes[0].model_copy()
    with pytest.raises(GeometryError, match="duplicate"):
        load_parcels(config.model_copy(update={"boxes": [*config.boxes, duplicate]}))


def test_reproject_round_trip_is_submillimetre():
    original = box(565500, 5308400, 565600, 5308500)
    back = reproject(reproject(original, "EPSG:25832", "EPSG:4326"), "EPSG:4326", "EPSG:25832")
    assert original.symmetric_difference(back).area < 1e-3
