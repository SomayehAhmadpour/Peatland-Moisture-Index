"""Ad-hoc point runs (dashboard "New analysis") and automatic tile choice."""

import json
from datetime import date

import pytest
from shapely.geometry import Point

from iris_ndwi.adhoc import metric_crs, point_config, run_point
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


def test_point_config_rejects_bad_dates(config):
    with pytest.raises(ValueError, match="end_date is before start_date"):
        point_config(config, 47.9, 9.9, 100, date(2024, 5, 1), date(2024, 4, 1), "x")


def test_dominant_tile_from_real_items(real_items):
    assert dominant_tile(real_items, Point(9.877, 47.926).buffer(0.001)) == "32UNU"
    assert dominant_tile([], Point(9.877, 47.926).buffer(0.001)) is None


def test_run_point_resolves_tile_and_writes_run(config, tmp_path):
    cfg = point_config(config, 47.926242, 9.877493, 100, date(2021, 1, 1), date(2026, 1, 1), "Bog test")
    result, run_dir = run_point(cfg, tmp_path, SyntheticSource())
    assert result.config.mgrs_tile == "32UNU"
    assert {o.parcel_id for o in result.observations} == {"point-box"}
    assert any(o.status == "accepted" for o in result.observations)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["config"]["mgrs_tile"] == "32UNU"
    assert {p.name for p in run_dir.iterdir()} >= {"observations.csv", "scenes.csv", "parcels.geojson", "manifest.json"}
