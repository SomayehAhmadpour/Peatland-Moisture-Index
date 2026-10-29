from datetime import datetime, timezone

import numpy as np
import pytest

from iris_ndwi.ndwi import PixelStatus
from iris_ndwi.zonal import summarize

WHEN = datetime(2024, 7, 1, tzinfo=timezone.utc)


def run(values, status, inside=None, min_valid_fraction=0.8, water_threshold=0.0):
    values = np.array(values, dtype=float)
    status = np.array(status, dtype=np.uint8)
    inside = np.ones(values.shape, bool) if inside is None else np.array(inside, bool)
    return summarize(
        parcel_id="p", item_id="i", acquired_at=WHEN, method_version="m", method_params_sha256="0123456789abcdef",
        inside=inside, values=values,
        status=status, min_valid_fraction=min_valid_fraction, water_threshold=water_threshold,
    )  # fmt: skip


V, C = PixelStatus.VALID, PixelStatus.SCL_MASKED


def test_all_valid_statistics():
    obs = run([[-0.5, -0.3], [-0.1, 0.1]], [[V, V], [V, V]])
    assert obs.status == "accepted" and obs.rejection_reason is None
    assert (obs.n_pixels, obs.n_valid, obs.valid_fraction) == (4, 4, 1.0)
    assert obs.ndwi_mean == pytest.approx(-0.2)
    assert obs.ndwi_median == pytest.approx(-0.2)
    assert (obs.ndwi_min, obs.ndwi_max) == (-0.5, 0.1)
    assert obs.water_fraction == pytest.approx(0.25)  # only 0.1 > 0


def test_fully_clouded_parcel_rejected_with_no_statistics():
    obs = run([[np.nan, np.nan]], [[C, C]])
    assert obs.status == "rejected" and obs.rejection_reason == "no_valid_pixels"
    assert obs.n_scl_masked == 2 and obs.ndwi_mean is None and obs.water_fraction is None


def test_valid_fraction_threshold_is_inclusive():
    values = [[-0.4] * 4 + [np.nan]]
    status = [[V] * 4 + [C]]  # 4/5 = 0.8 valid
    assert run(values, status, min_valid_fraction=0.8).status == "accepted"
    rejected = run(values, status, min_valid_fraction=0.81)
    assert rejected.rejection_reason == "valid_fraction_below_min"
    assert rejected.ndwi_mean == pytest.approx(-0.4)  # statistics kept for audit


def test_parcel_outside_window_has_no_pixels():
    obs = run([[-0.4]], [[V]], inside=[[False]])
    assert obs.rejection_reason == "parcel_has_no_pixels" and obs.n_pixels == 0


def test_only_pixels_inside_parcel_are_counted():
    obs = run([[-0.4, 0.9]], [[V, V]], inside=[[True, False]])
    assert obs.n_pixels == 1 and obs.ndwi_max == pytest.approx(-0.4)


def test_counts_add_up():
    statuses = [list(PixelStatus)]  # one pixel of every status
    obs = run([[-0.4] + [np.nan] * (len(PixelStatus) - 1)], statuses, min_valid_fraction=0.1)
    total = obs.n_valid + obs.n_scl_masked + obs.n_bright_surface + obs.n_nodata
    total += obs.n_non_positive_reflectance + obs.n_zero_denominator + obs.n_out_of_range
    assert total == obs.n_pixels == len(PixelStatus)
    assert obs.n_bright_surface == 1


def test_stable_observation_id():
    assert run([[-0.4]], [[V]]).observation_id == "p:i:m:01234567"
