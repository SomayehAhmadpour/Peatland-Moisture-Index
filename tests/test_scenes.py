import copy

import pytest
from shapely.geometry import box, mapping

from iris_ndwi.scenes import SceneMetadataError, aoi_coverage, parse_item, select_scenes


def by_id(items, item_id):
    return next(i for i in items if i["id"] == item_id)


def test_parse_real_legacy_item_with_offset_already_applied(real_items):
    """Legacy item flagged boa_offset_applied=true advertises -0.1, but its DN are
    already harmonised, so the pipeline applies no further offset."""
    item = by_id(real_items, "S2A_32UNU_20260929_0_L2A")
    assert item["assets"]["green"]["raster:bands"][0]["offset"] == -0.1  # as published
    scene = parse_item(item)
    assert scene.mgrs_tile == "32UNU"
    assert scene.crs == "EPSG:32632"
    assert scene.processing_baseline == "05.13"
    assert scene.boa_offset_applied_flag is True
    assert (scene.green.scale, scene.green.offset, scene.green.nodata) == (0.0001, 0.0, 0)
    assert scene.green.href.endswith("/B03.tif") and scene.nir.href.endswith("/B08.tif")
    assert scene.scl.href.endswith("/SCL.tif") and scene.scl.offset == 0.0
    assert scene.blue.href.endswith("/B02.tif") and scene.blue.offset == 0.0
    assert scene.acquired_at.tzinfo is not None


def test_parse_real_legacy_item_without_applied_flag_keeps_offset(real_items):
    scene = parse_item(by_id(real_items, "S2B_32UNU_20220225_0_L2A"))
    assert scene.processing_baseline == "04.00" and scene.boa_offset_applied_flag is False
    assert scene.green.offset == -0.1 and scene.nir.offset == -0.1


def test_collection_priority_prefers_c1_for_same_acquisition(real_items, c1_items):
    """2021-05-31 S2B exists in both collections; the configured first collection wins."""
    order = ["sentinel-2-c1-l2a", "sentinel-2-l2a"]
    selected, rejected = select_scenes([*real_items, *c1_items], "32UNU", 100, None, order)
    day = [s for s in selected if s.acquired_at.strftime("%Y%m%d") == "20210531"]
    assert [s.item_id for s in day] == ["S2B_T32UNU_20210531T102618_L2A"]
    legacy = {r.item_id: r.reason for r in rejected}
    assert legacy["S2B_32UNU_20210531_1_L2A"] == "superseded_by:S2B_T32UNU_20210531T102618_L2A"
    reversed_order, _ = select_scenes([*real_items, *c1_items], "32UNU", 100, None, order[::-1])
    assert "S2B_32UNU_20210531_1_L2A" in {s.item_id for s in reversed_order}


def test_parse_real_item_pre_04_baseline_has_zero_offset(real_items):
    scene = parse_item(by_id(real_items, "S2B_32UNU_20210531_0_L2A"))
    assert scene.processing_baseline == "03.00"
    assert scene.green.offset == 0.0


def test_reprocessed_duplicate_keeps_highest_baseline(real_items):
    """Real case: 2021-05-31 exists as baseline 03.00 (_0) and reprocessed 05.00 (_1),
    with datetimes 1 ms apart."""
    selected, rejected = select_scenes(real_items, "32UNU", max_scene_cloud_pct=100)
    ids = [s.item_id for s in selected]
    assert "S2B_32UNU_20210531_1_L2A" in ids
    assert "S2B_32UNU_20210531_0_L2A" not in ids
    reasons = {r.item_id: r.reason for r in rejected}
    assert reasons["S2B_32UNU_20210531_0_L2A"] == "superseded_by:S2B_32UNU_20210531_1_L2A"


def test_same_day_different_platform_is_not_a_duplicate(real_items):
    item = copy.deepcopy(by_id(real_items, "S2A_32UNU_20260929_0_L2A"))
    item["id"] = "S2B_32UNU_20260929_0_L2A"
    item["properties"]["platform"] = "sentinel-2b"
    selected, _ = select_scenes([*real_items, item], "32UNU", 100)
    assert {"S2A_32UNU_20260929_0_L2A", "S2B_32UNU_20260929_0_L2A"} <= {s.item_id for s in selected}


def test_cloud_prefilter_and_boundary(real_items):
    item = by_id(real_items, "S2A_32UNU_20260929_0_L2A")
    cloud = item["properties"]["eo:cloud_cover"]
    kept, _ = select_scenes([item], "32UNU", max_scene_cloud_pct=cloud)  # equal -> kept
    dropped, rejected = select_scenes([item], "32UNU", max_scene_cloud_pct=cloud - 0.001)
    assert len(kept) == 1 and dropped == []
    assert rejected[0].reason == "scene_cloud_cover_above_max"


def test_other_tile_rejected(real_items):
    selected, rejected = select_scenes(real_items, "32TNT", 100)
    assert selected == []
    assert {r.reason for r in rejected} == {"other_mgrs_tile"}


def test_missing_asset_is_recorded_not_raised(real_items):
    broken = copy.deepcopy(real_items[0])
    del broken["assets"]["scl"]
    selected, rejected = select_scenes([broken], "32UNU", 100)
    assert selected == []
    assert rejected[0].reason.startswith("metadata_error") and "scl" in rejected[0].reason


def test_missing_scale_raises(real_items):
    broken = copy.deepcopy(real_items[0])
    del broken["assets"]["green"]["raster:bands"][0]["scale"]
    with pytest.raises(SceneMetadataError, match="scale"):
        parse_item(broken)


def test_missing_properties_raise(real_items):
    broken = copy.deepcopy(real_items[0])
    del broken["properties"]["s2:processing_baseline"]
    with pytest.raises(SceneMetadataError, match="processing_baseline"):
        parse_item(broken)


def test_parse_c1_item_uses_proj_code(c1_items):
    scene = parse_item(by_id(c1_items, "S2B_T32UNU_20260813T101556_L2A"))
    assert scene.crs == "EPSG:32632" and scene.mgrs_tile == "32UNU"
    assert scene.green.offset == -0.1 and scene.footprint is not None


def test_c1_collection_is_radiometrically_uniform(c1_items):
    """Every C1 item is baseline >= 05.00 and advertises the same scale/offset."""
    scenes = [parse_item(item) for item in c1_items]
    assert all(s.baseline_key >= (5, 0) for s in scenes)
    assert {(s.green.scale, s.green.offset, s.nir.scale, s.nir.offset) for s in scenes} == {(0.0001, -0.1, 0.0001, -0.1)}


def test_split_datastrip_pair_keeps_more_complete_product(c1_items):
    """Real case: one S2A pass on 2023-06-25 delivered as two products (7 % vs 76 % tile nodata)."""
    aoi = box(9.83, 47.90, 9.93, 47.95)
    selected, rejected = select_scenes(c1_items, "32UNU", 100, aoi)
    ids = {s.item_id for s in selected}
    assert "S2A_T32UNU_20230625T101601_L2A" in ids
    assert "S2A_T32UNU_20230625T102734_L2A" not in ids
    reasons = {r.item_id: r.reason for r in rejected}
    assert reasons["S2A_T32UNU_20230625T102734_L2A"] == "superseded_by:S2A_T32UNU_20230625T101601_L2A"


def test_aoi_coverage_beats_baseline(c1_items):
    """A product that misses the AOI loses even with a newer baseline."""
    aoi = box(9.83, 47.90, 9.93, 47.95)
    partial = copy.deepcopy(by_id(c1_items, "S2A_T32UNU_20230625T101601_L2A"))
    partial["properties"]["s2:processing_baseline"] = "09.99"
    partial["geometry"] = mapping(box(9.70, 47.90, 9.85, 47.95))  # covers 20 % of the AOI
    other = by_id(c1_items, "S2A_T32UNU_20230625T102734_L2A")
    selected, _ = select_scenes([partial, other], "32UNU", 100, aoi)
    assert [s.item_id for s in selected] == ["S2A_T32UNU_20230625T102734_L2A"]
    assert aoi_coverage(parse_item(partial), aoi) == pytest.approx(0.2)


def test_selection_is_order_independent(real_items):
    a, _ = select_scenes(real_items, "32UNU", 100)
    b, _ = select_scenes(list(reversed(real_items)), "32UNU", 100)
    assert [s.item_id for s in a] == [s.item_id for s in b]
