"""End-to-end runs on the synthetic source (no network)."""

import csv
import json
from datetime import date

import pytest

from iris_ndwi.outputs import write_outputs
from iris_ndwi.parcels import load_parcels
from iris_ndwi.pipeline import run_pipeline
from iris_ndwi.report import quality_summary
from iris_ndwi.synthetic import ID_PREFIX, SCENARIOS, Scenario, SyntheticSource


@pytest.fixture
def result(boxes_only_config):
    return run_pipeline(boxes_only_config, SyntheticSource(), load_parcels(boxes_only_config))


def obs_for(result, parcel_id, day):
    return next(o for o in result.observations if o.parcel_id == parcel_id and o.acquired_at.strftime("%Y%m%d") == day)


def test_scene_ledger_accounts_for_every_item(result):
    assert result.n_items_found == len(SCENARIOS)
    assert len(result.scenes) + len(result.scene_rejections) == len(SCENARIOS)
    reasons = {r.item_id.removeprefix("SYNTHETIC_"): r.reason.split(":")[0] for r in result.scene_rejections}
    assert reasons == {
        "S2A_32UNU_20230712_0_L2A": "superseded_by",
        "S2A_32UNU_20240801_0_L2A": "scene_cloud_cover_above_max",
        "S2A_32TNT_20240801_0_L2A": "other_mgrs_tile",
        "S2A_32UNU_20241010_0_L2A": "read_error",
        "S2B_32UNU_20250604_0_L2A": "radiometric_offset_inconsistent",
    }


def test_synthetic_ids_cannot_collide_with_real_ones(result):
    ids = [s.item_id for s in result.scenes] + [r.item_id for r in result.scene_rejections]
    assert ids and all(i.startswith("SYNTHETIC_") for i in ids)
    assert all(o.observation_id.split(":")[1].startswith("SYNTHETIC_") for o in result.observations)


def test_boxes_have_exactly_100_pixels_in_every_scene(result):
    assert {o.n_pixels for o in result.observations} == {100}


def test_snow_scene_rejected(result):
    obs = obs_for(result, "wr-core-100m", "20240115")
    assert obs.rejection_reason == "no_valid_pixels" and obs.n_scl_masked == 100


def test_fog_missed_by_scl_is_rejected_by_blue_test(result):
    obs = obs_for(result, "wr-core-100m", "20251112")
    assert obs.rejection_reason == "no_valid_pixels" and obs.n_bright_surface == 100 and obs.n_scl_masked == 0


def test_clear_scenes_accepted_and_plausible(result):
    obs = obs_for(result, "wr-core-100m", "20210710")
    assert obs.status == "accepted" and obs.valid_fraction == 1.0
    assert -1 <= obs.ndwi_min <= obs.ndwi_median <= obs.ndwi_max <= 1


def test_temporal_comparability_across_baseline_change(boxes_only_config):
    """Same surface (same seed) stored as baseline 03.01 and 05.10 -> same NDWI."""
    scenarios = (
        Scenario("S2B_32UNU_20210710_0_L2A", "2021-07-10T10:27:00Z", "03.01"),
        Scenario("S2B_32UNU_20240710_0_L2A", "2024-07-10T10:27:00Z", "05.10"),
    )
    result = run_pipeline(boxes_only_config, SyntheticSource(scenarios), load_parcels(boxes_only_config))
    before = obs_for(result, "wr-core-100m", "20210710")
    after = obs_for(result, "wr-core-100m", "20240710")
    assert before.ndwi_median == pytest.approx(after.ndwi_median, abs=2e-4)  # DN rounding only


def test_run_is_deterministic(boxes_only_config, result):
    again = run_pipeline(boxes_only_config, SyntheticSource(), load_parcels(boxes_only_config))
    assert [o.as_row() for o in again.observations] == [o.as_row() for o in result.observations]
    assert again.config_sha256 == result.config_sha256


def test_outputs_written(result, tmp_path):
    run_dir = write_outputs(result, tmp_path)
    with open(run_dir / "observations.csv") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == len(result.observations)
    assert len({r["observation_id"] for r in rows}) == len(rows)  # stable unique ids
    with open(run_dir / "scenes.csv") as handle:
        scenes = {r["item_id"].removeprefix(ID_PREFIX): r for r in csv.DictReader(handle)}
    assert len(scenes) == len(SCENARIOS)
    # every band that was read is in the ledger, B02 (fog test) included
    assert scenes["S2B_32UNU_20210710_0_L2A"]["blue_href"].endswith("/B02.tif")
    # rejected items keep their metadata, so the ledger shows what was dropped
    superseded = scenes["S2A_32UNU_20230712_0_L2A"]
    assert superseded["selected"] == "False" and superseded["collection"] and superseded["processing_baseline"]
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["synthetic_data"] is True
    assert manifest["method_params_sha256"] == result.method_params_sha256
    parcels = json.loads((run_dir / "parcels.geojson").read_text())
    assert parcels["crs"]["properties"]["name"].endswith("25832")


def test_quality_summary_flags_synthetic_data(result):
    text = quality_summary(result)
    assert "SYNTHETIC DATA" in text and "read_error" in text


def test_empty_period_produces_empty_but_valid_result(boxes_only_config):
    cfg = boxes_only_config.model_copy(update={"start_date": date(2030, 1, 1), "end_date": date(2030, 2, 1)})
    result = run_pipeline(cfg, SyntheticSource(), load_parcels(cfg))
    assert result.n_items_found == 0 and result.observations == []
    assert "0 STAC items found" in quality_summary(result)


def test_observation_ids_change_with_thresholds_only(boxes_only_config, result):
    """Other thresholds -> new ids (rows are added, not overwritten); another period -> same ids."""
    stricter = boxes_only_config.model_copy(update={"max_blue_reflectance": 0.10})
    other = run_pipeline(stricter, SyntheticSource(), load_parcels(stricter))
    assert other.method_params_sha256 != result.method_params_sha256
    assert not {o.observation_id for o in other.observations} & {o.observation_id for o in result.observations}

    later = boxes_only_config.model_copy(update={"start_date": date(2024, 1, 1)})
    again = run_pipeline(later, SyntheticSource(), load_parcels(later))
    assert again.config_sha256 != result.config_sha256
    assert {o.observation_id for o in again.observations} < {o.observation_id for o in result.observations}


class BrokenSource(SyntheticSource):
    """Fails on one scene with an error that is neither a read error nor a quality check."""

    def read(self, scene, bounds):
        if scene.item_id.endswith("20240618_0_L2A"):
            raise ValueError("unexpected band shape")
        return super().read(scene, bounds)


def test_unexpected_error_rejects_one_scene_not_the_run(boxes_only_config, result):
    broken = run_pipeline(boxes_only_config, BrokenSource(), load_parcels(boxes_only_config))
    reasons = {r.item_id.removeprefix(ID_PREFIX): r.reason for r in broken.scene_rejections}
    assert reasons["S2B_32UNU_20240618_0_L2A"] == "processing_error: ValueError: unexpected band shape"
    assert len(broken.scenes) == len(result.scenes) - 1


def test_quality_summary_has_a_monitoring_section(result):
    text = quality_summary(result)
    assert "## Monitoring: latest accepted value against the same month in other years" in text
    assert "| wr-core-100m |" in text.split("## Monitoring")[1]
