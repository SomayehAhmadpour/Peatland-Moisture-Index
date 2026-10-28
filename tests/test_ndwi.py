import numpy as np
import pytest
from conftest import NO_OFFSET_BAND, OFFSET_BAND

from iris_ndwi.ndwi import PixelStatus, below_offset_fraction, classify_pixels, ndwi, to_reflectance

VALID_SCL = [4, 5, 6]


def classify(green_dn, nir_dn, scl, band=OFFSET_BAND, require_positive=True, blue_dn=None, max_blue=None):
    green_dn = np.array(green_dn, dtype=np.uint16)
    # default blue: a dark, clear surface (0.03 reflectance) in the same DN encoding
    blue_dn = np.full(green_dn.shape, round((0.03 - band.offset) / band.scale)) if blue_dn is None else blue_dn
    return classify_pixels(
        np.array(blue_dn, dtype=np.uint16),
        green_dn,
        np.array(nir_dn, dtype=np.uint16),
        np.array(scl, dtype=np.uint8),
        band,
        band,
        band,
        scl_nodata=0,
        valid_scl_classes=VALID_SCL,
        require_positive_reflectance=require_positive,
        max_blue_reflectance=max_blue,
    )


def test_ndwi_known_values():
    assert ndwi(0.1, 0.3) == pytest.approx(-0.5)
    assert ndwi(0.3, 0.1) == pytest.approx(0.5)
    assert ndwi(0.2, 0.2) == pytest.approx(0.0)


def test_zero_denominator_gives_nan_without_warning():
    with np.errstate(all="raise"):  # any divide warning would raise here
        result = ndwi(np.array([0.0, -0.05, 0.1]), np.array([0.0, 0.05, 0.3]))
    assert np.isnan(result[0]) and np.isnan(result[1])
    assert result[2] == pytest.approx(-0.5)
    assert not np.isinf(result).any()


def test_zero_denominator_status_when_negative_reflectance_allowed():
    # green = 900 DN -> -0.01, nir = 1100 DN -> +0.01: denominator exactly 0
    values, status = classify([[900]], [[1100]], [[4]], require_positive=False)
    assert status[0, 0] == PixelStatus.ZERO_DENOMINATOR
    assert np.isnan(values[0, 0])


def test_out_of_range_when_negative_reflectance_allowed():
    # green -0.01, nir +0.03 -> (-0.04)/(0.02) = -2
    values, status = classify([[900]], [[1300]], [[4]], require_positive=False)
    assert status[0, 0] == PixelStatus.OUT_OF_RANGE
    assert np.isnan(values[0, 0])


def test_non_positive_reflectance_rejected_by_default():
    values, status = classify([[900, 1000]], [[2000, 2000]], [[6, 6]])
    assert list(status[0]) == [PixelStatus.NON_POSITIVE_REFLECTANCE] * 2  # -0.01 and exactly 0.0
    assert np.isnan(values).all()


def test_offset_harmonisation_makes_baselines_comparable():
    """The same surface stored before and after baseline 04.00 gives identical NDWI."""
    green_reflectance, nir_reflectance = 0.06, 0.30
    old_values, _ = classify([[600]], [[3000]], [[4]], band=NO_OFFSET_BAND)
    new_values, _ = classify([[1600]], [[4000]], [[4]], band=OFFSET_BAND)
    expected = (green_reflectance - nir_reflectance) / (green_reflectance + nir_reflectance)
    assert old_values[0, 0] == pytest.approx(expected)
    assert new_values[0, 0] == pytest.approx(expected)


def test_ignoring_offset_would_bias_ndwi():
    """Guard against regressions: applying no offset to new data changes NDWI materially."""
    biased, _ = classify([[1600]], [[4000]], [[4]], band=NO_OFFSET_BAND)
    correct, _ = classify([[1600]], [[4000]], [[4]], band=OFFSET_BAND)
    assert abs(biased[0, 0] - correct[0, 0]) > 0.05


@pytest.mark.parametrize("scl_class", [1, 2, 3, 7, 8, 9, 10, 11])
def test_scl_classes_masked(scl_class):
    values, status = classify([[1600]], [[4000]], [[scl_class]])
    assert status[0, 0] == PixelStatus.SCL_MASKED
    assert np.isnan(values[0, 0])


@pytest.mark.parametrize("scl_class", VALID_SCL)
def test_scl_classes_valid(scl_class):
    _, status = classify([[1600]], [[4000]], [[scl_class]])
    assert status[0, 0] == PixelStatus.VALID


def test_nodata_has_priority_over_other_reasons():
    # nodata DN in green, SCL says cloud: reported as NODATA (first rule wins)
    _, status = classify([[0, 1600, 1600]], [[4000, 0, 4000]], [[9, 4, 0]])
    assert list(status[0]) == [PixelStatus.NODATA] * 3


def test_to_reflectance_nodata_is_nan():
    reflectance = to_reflectance(np.array([0, 1000, 11000], dtype=np.uint16), OFFSET_BAND)
    assert np.isnan(reflectance[0])
    assert reflectance[1:] == pytest.approx([0.0, 1.0])


def test_below_offset_fraction_detects_double_offset():
    """Real failure mode in Earth Search `sentinel-2-l2a`: DN already harmonised
    (bog green ~ 660 DN) while metadata still advertises offset -0.1."""
    scl = np.full((2, 2), 4, np.uint8)
    harmonised = below_offset_fraction(
        np.full((2, 2), 660, np.uint16), np.full((2, 2), 2500, np.uint16), scl, OFFSET_BAND, OFFSET_BAND, VALID_SCL
    )
    with_offset = below_offset_fraction(
        np.full((2, 2), 1660, np.uint16), np.full((2, 2), 3500, np.uint16), scl, OFFSET_BAND, OFFSET_BAND, VALID_SCL
    )
    assert harmonised == 1.0 and with_offset == 0.0


def test_below_offset_fraction_ignores_clouds_nodata_and_offsetless_scenes():
    green = np.array([[660, 0, 1660]], np.uint16)
    nir = np.array([[2500, 0, 3500]], np.uint16)
    scl = np.array([[9, 0, 4]], np.uint8)  # the low pixel is cloud -> not counted
    assert below_offset_fraction(green, nir, scl, OFFSET_BAND, OFFSET_BAND, VALID_SCL) == 0.0
    assert below_offset_fraction(green, nir, scl, NO_OFFSET_BAND, NO_OFFSET_BAND, VALID_SCL) is None
    all_cloud = np.full((1, 3), 9, np.uint8)
    assert below_offset_fraction(green, nir, all_cloud, OFFSET_BAND, OFFSET_BAND, VALID_SCL) is None


def test_bright_fog_labelled_land_is_masked():
    """Real case 2021-11-12: fog over the bog, SCL 5, blue ~0.22 -> NDWI ~ -0.12."""
    fog = [[3200, 1500]]  # blue 0.22 (fog) and 0.05 (clear), offset -0.1
    values, status = classify([[3200, 1600]], [[3600, 4000]], [[5, 5]], blue_dn=fog, max_blue=0.12)
    assert list(status[0]) == [PixelStatus.BRIGHT_SURFACE, PixelStatus.VALID]
    assert np.isnan(values[0, 0]) and values[0, 1] == pytest.approx((0.06 - 0.30) / (0.06 + 0.30))


def test_bright_surface_threshold_is_exclusive_and_can_be_disabled():
    at_threshold = [[2200]]  # blue exactly 0.12
    _, status = classify([[1600]], [[4000]], [[4]], blue_dn=at_threshold, max_blue=0.12)
    assert status[0, 0] == PixelStatus.VALID
    _, status = classify([[1600]], [[4000]], [[4]], blue_dn=[[9000]], max_blue=None)
    assert status[0, 0] == PixelStatus.VALID


def test_scl_cloud_wins_over_bright_surface():
    _, status = classify([[1600]], [[4000]], [[9]], blue_dn=[[9000]], max_blue=0.12)
    assert status[0, 0] == PixelStatus.SCL_MASKED


def test_shape_mismatch_raises():
    with pytest.raises(ValueError, match="shapes differ"):
        classify([[1, 2]], [[1]], [[4]])
