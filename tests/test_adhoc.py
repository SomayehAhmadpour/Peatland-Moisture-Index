"""Ad-hoc point runs (dashboard "New analysis") and automatic tile choice."""

import json
from datetime import date

import pytest
from pyproj import Geod
from shapely.geometry import Point, box, mapping

from iris_ndwi.adhoc import (
    MAX_AREA_KM2, area_config, box_parcel_id, is_single_box, metric_crs, parse_coordinate, point_config, rerun_config,
    run_and_write,
)  # fmt: skip
from iris_ndwi.config import BoxParcel
from iris_ndwi.parcels import GeometryError, load_parcels
from iris_ndwi.pipeline import run_pipeline
from iris_ndwi.scenes import dominant_tile
from iris_ndwi.synthetic import SyntheticSource


@pytest.mark.parametrize(
    ("lat", "lon", "crs"),
    [
        (47.926, 9.877, "EPSG:25832"),  # Wurzacher Ried: the project CRS
        (52.52, 13.40, "EPSG:25833"),  # Berlin, ETRS89 / UTM 33N
        (40.71, -74.01, "EPSG:32618"),  # New York, WGS 84 / UTM 18N
        (-33.87, 151.21, "EPSG:32756"),  # Sydney, WGS 84 / UTM 56S
    ],
)
def test_metric_crs(lat, lon, crs):
    assert metric_crs(lat, lon) == crs


def test_point_config_keeps_quality_settings(config):
    cfg = point_config(config, 52.52, 13.40, 200, date(2024, 5, 1), date(2024, 8, 31), "Test box")
    assert cfg.mgrs_tile is None and cfg.parcels_geojson is None
    assert cfg.target_crs == "EPSG:25833"
    assert (cfg.min_valid_fraction, cfg.max_blue_reflectance) == (config.min_valid_fraction, config.max_blue_reflectance)
    (parcel,) = load_parcels(cfg)
    assert parcel.area_m2 == pytest.approx(200 * 200)


def test_box_parcel_id_is_valid_and_unique():
    ids = {box_parcel_id(-33.87, -70.6, 100), box_parcel_id(33.87, 70.6, 100), box_parcel_id(33.87, 70.6, 150.5)}
    assert ids == {"point-33p87000s-70p60000w-100m", "point-33p87000n-70p60000e-100m", "point-33p87000n-70p60000e-150p5m"}
    for parcel_id in ids:
        BoxParcel(parcel_id=parcel_id, name="x", kind="point", center_lat=0, center_lon=0, size_m=100)


def test_point_config_rejects_bad_dates(config):
    with pytest.raises(ValueError, match="end_date is before start_date"):
        point_config(config, 47.9, 9.9, 100, date(2024, 5, 1), date(2024, 4, 1), "x")


def test_dominant_tile_from_real_items(real_items):
    assert dominant_tile(real_items, Point(9.877, 47.926).buffer(0.001)) == "32UNU"
    assert dominant_tile([], Point(9.877, 47.926).buffer(0.001)) is None


def test_run_point_resolves_tile_and_writes_run(config, tmp_path):
    cfg = point_config(config, 47.926242, 9.877493, 100, date(2021, 1, 1), date(2026, 1, 1), "Bog test")
    result, run_dir = run_and_write(cfg, tmp_path, SyntheticSource())
    assert result.config.mgrs_tile == "32UNU"
    assert {o.parcel_id for o in result.observations} == {"point-47p92624n-9p87749e-100m"}
    assert any(o.status == "accepted" for o in result.observations)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["config"]["mgrs_tile"] == "32UNU"
    assert {p.name for p in run_dir.iterdir()} >= {"observations.csv", "scenes.csv", "parcels.geojson", "manifest.json"}


def test_rerun_config_changes_only_the_dates(config, tmp_path):
    _, run_dir = run_and_write(config, tmp_path, SyntheticSource())
    manifest = json.loads((run_dir / "manifest.json").read_text())
    root = config.parcels_geojson.parents[2]  # repository root
    manifest["config"]["parcels_geojson"] = "data/parcels/wurzacher_ried_osm.geojson"  # as committed: relative
    cfg = rerun_config(manifest["config"], date(2024, 1, 1), date(2024, 12, 31), root)
    assert (cfg.start_date, cfg.end_date) == (date(2024, 1, 1), date(2024, 12, 31))
    assert cfg.parcels_geojson == root / "data/parcels/wurzacher_ried_osm.geojson"
    unchanged = {"start_date", "end_date", "parcels_geojson"}
    assert cfg.model_dump(exclude=unchanged) == config.model_dump(exclude=unchanged)
    assert [p.parcel_id for p in load_parcels(cfg)] == [p.parcel_id for p in load_parcels(config)]


def test_rerun_config_rejects_bad_dates(config):
    with pytest.raises(ValueError, match="end_date is before start_date"):
        rerun_config(config.model_dump(mode="json"), date(2024, 5, 1), date(2024, 4, 1), config.parcels_geojson.parents[2])


def test_rerun_config_resizes_a_single_box(config):
    point = point_config(config, 47.926242, 9.877493, 100, date(2024, 5, 1), date(2024, 8, 31), "Box")
    manifest_config = point.model_dump(mode="json")
    assert is_single_box(manifest_config)
    cfg = rerun_config(manifest_config, date(2024, 5, 1), date(2024, 8, 31), config.parcels_geojson.parents[2], size_m=300)
    (before,), (after,) = load_parcels(point), load_parcels(cfg)
    assert after.area_m2 == pytest.approx(300 * 300)
    assert (before.parcel_id, after.parcel_id) == ("point-47p92624n-9p87749e-100m", "point-47p92624n-9p87749e-300m")
    assert after.geometry.centroid.distance(before.geometry.centroid) <= 10  # same centre, within grid snapping


def test_rerun_config_refuses_to_resize_multi_parcel_runs(config):
    manifest_config = config.model_dump(mode="json")
    assert not is_single_box(manifest_config)
    with pytest.raises(ValueError, match="exactly one box"):
        rerun_config(manifest_config, date(2024, 1, 1), date(2024, 2, 1), config.parcels_geojson.parents[2], size_m=300)


@pytest.mark.parametrize(
    ("text", "axis", "value"),
    [
        ("131.4833", "lon", 131.4833),
        ("131°29′", "lon", 131 + 29 / 60),
        ("132°15′ E", "lon", 132.25),
        ("46 59 N", "lat", 46 + 59 / 60),
        ("46°28'30\"N", "lat", 46.475),
        ("-70.6", "lon", -70.6),
        ("W70.6", "lon", -70.6),
        ("33°52′ S", "lat", -(33 + 52 / 60)),
    ],
)
def test_parse_coordinate(text, axis, value):
    assert parse_coordinate(text, axis) == pytest.approx(value)


@pytest.mark.parametrize(
    ("text", "axis"), [("46 N", "lon"), ("10 E", "lat"), ("-46 S", "lat"), ("91", "lat"), ("46 75", "lat"), ("abc", "lon"), ("", "lat")]
)
def test_parse_coordinate_rejects(text, axis):
    with pytest.raises(ValueError):
        parse_coordinate(text, axis)


def test_area_config_builds_the_rectangle(config):
    cfg = area_config(config, 9.86, 47.92, 9.89, 47.935, date(2024, 5, 1), date(2024, 8, 31), "Ried")
    assert (cfg.boxes, cfg.mgrs_tile, cfg.target_crs) == ([], None, "EPSG:25832")
    (parcel,) = load_parcels(cfg)
    assert (parcel.parcel_id, parcel.kind) == ("area-9p86000e-47p92000n-9p89000e-47p93500n", "area")
    geodesic, _ = Geod(ellps="WGS84").geometry_area_perimeter(box(9.86, 47.92, 9.89, 47.935))
    assert parcel.area_m2 == pytest.approx(abs(geodesic), rel=0.002)  # UTM scale error only


def test_area_config_uses_the_local_utm_zone_and_limits_the_size(config):
    cfg = area_config(config, 131.5, 46.5, 131.6, 46.6, date(2024, 5, 1), date(2024, 8, 31), "Far east")
    assert cfg.target_crs == "EPSG:32652"
    with pytest.raises(GeometryError, match=f"limit is {MAX_AREA_KM2} km²"):  # the 131°29′–132°15′ E, 46°28′–46°59′ N example
        area_config(config, 131 + 29 / 60, 46 + 28 / 60, 132.25, 46 + 59 / 60, date(2024, 5, 1), date(2024, 8, 31), "Big")
    with pytest.raises(ValueError, match="east must be greater than west"):
        area_config(config, 10.0, 47.0, 9.9, 47.1, date(2024, 5, 1), date(2024, 8, 31), "x")


def test_area_run_and_rerun(config, tmp_path):
    cfg = area_config(config, 9.87, 47.922, 9.885, 47.93, date(2021, 1, 1), date(2026, 1, 1), "Ried")
    result, run_dir = run_and_write(cfg, tmp_path, SyntheticSource())
    assert result.config.mgrs_tile == "32UNU" and any(o.status == "accepted" for o in result.observations)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert not is_single_box(manifest["config"])  # an area is re-run with new dates only
    again = rerun_config(manifest["config"], date(2024, 1, 1), date(2024, 12, 31), tmp_path)
    assert load_parcels(again)[0].geometry.equals(result.parcels[0].geometry)


def test_area_outside_every_tile_is_an_error(config, real_items):
    # a real 32UNU item with its footprint (the fixtures leave footprints out); reading is never reached
    item = {**real_items[0], "geometry": mapping(box(9.0, 47.7, 10.5, 48.7))}

    class RecordedItems:
        name = "recorded"

        def search(self, aoi_wgs84, start, end):
            return [item]

    assert dominant_tile([item], box(9.86, 47.92, 9.89, 47.935)) == "32UNU"  # an area inside the tile
    berlin = area_config(config, 13.3, 52.4, 13.4, 52.5, date(2021, 1, 1), date(2026, 1, 1), "Berlin")
    with pytest.raises(ValueError, match="no single Sentinel-2 tile"):
        run_pipeline(berlin, RecordedItems(), load_parcels(berlin))
