from datetime import datetime, timezone

from iris_ndwi.interpret import Obs, interpret


def obs(year, month, day, value, status="accepted", reason=None):
    return Obs(
        acquired_at=datetime(year, month, day, tzinfo=timezone.utc),
        status=status,
        ndwi_median=value if status == "accepted" else None,
        ndwi_p25=value - 0.01 if status == "accepted" else None,
        ndwi_p75=value + 0.01 if status == "accepted" else None,
        valid_fraction=1.0 if status == "accepted" else 0.0,
        rejection_reason=reason,
    )


SERIES = [
    obs(2022, 7, 10, -0.66),
    obs(2023, 7, 12, -0.65),
    obs(2024, 7, 9, -0.64),
    obs(2025, 6, 20, -0.62),
    obs(2025, 7, 15, -0.58),
    obs(2025, 7, 25, None, "rejected", "no_valid_pixels"),
]


def texts(notes):
    return [n.text for n in notes]


def test_latest_change_and_seasonal_position():
    notes = texts(interpret(SERIES))
    assert notes[0].startswith("Latest accepted observation 2025-07-15: median NDWI -0.580")
    assert notes[1].startswith("0.040 higher (wetter) than the previous accepted observation (2025-06-20, 25 days earlier)")
    assert notes[2].startswith("Above the typical July range of other years")
    assert "n=3" in notes[2]


def test_newer_rejections_are_flagged():
    warnings = [n.text for n in interpret(SERIES) if n.level == "warning"]
    assert any("1 newer scene(s) rejected (no_valid_pixels)" in w and "10 days old" in w for w in warnings)


def test_too_few_seasonal_samples():
    notes = interpret(SERIES[2:5])
    assert any(n.level == "warning" and "no seasonal comparison" in n.text for n in notes)


def test_as_of_limits_series():
    notes = texts(interpret(SERIES, as_of=datetime(2024, 12, 31, tzinfo=timezone.utc)))
    assert notes[0].startswith("Latest accepted observation 2024-07-09")


def test_no_accepted_observations():
    notes = interpret([obs(2025, 1, 1, None, "rejected", "no_valid_pixels")])
    assert [n.level for n in notes] == ["warning"]


def test_deterministic():
    assert interpret(SERIES) == interpret(list(reversed(SERIES)))
