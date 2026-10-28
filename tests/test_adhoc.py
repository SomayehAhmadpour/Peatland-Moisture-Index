"""Ad-hoc point runs (dashboard "New analysis") and automatic tile choice."""

import json
from datetime import date

import pytest
from shapely.geometry import Point

from iris_ndwi.adhoc import box_parcel_id, is_single_box, metric_crs, point_config, rerun_config, run_and_write
from iris_ndwi.config import BoxParcel
from iris_ndwi.parcels import load_parcels
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
