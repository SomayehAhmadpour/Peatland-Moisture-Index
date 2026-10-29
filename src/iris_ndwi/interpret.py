"""Rule-based, deterministic interpretation of a parcel's NDWI series.

Every sentence is produced from fixed templates and the observation
statistics, so the same data always gives the same text (no LLM).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median, quantiles

MIN_SEASONAL_SAMPLES = 3


@dataclass(frozen=True)
class Obs:
    acquired_at: datetime
    status: str
    ndwi_median: float | None
    ndwi_p25: float | None
    ndwi_p75: float | None
    valid_fraction: float
    rejection_reason: str | None = None


@dataclass(frozen=True)
class Note:
    level: str  # info | warning
    text: str


@dataclass(frozen=True)
class SeasonalCheck:
    """The latest accepted value against the same calendar month in other years."""

    latest: Obs
    n: int  # accepted observations of that month in other years
    position: str | None = None  # above | within | below; None when n < MIN_SEASONAL_SAMPLES
    q1: float | None = None
    q3: float | None = None
    median: float | None = None


def seasonal_check(accepted: list[Obs]) -> SeasonalCheck:
    """Compare the last of `accepted` (sorted by time, all with a median) with its month in other years.

    Comparing within a calendar month removes most of the seasonal cycle of
    vegetation and sun angle, so "below" flags a parcel that is drier than usual
    for the time of year, not merely drier than in summer.
    """
    latest = accepted[-1]
    same_month = [
        o.ndwi_median
        for o in accepted
        if o.acquired_at.month == latest.acquired_at.month and o.acquired_at.year != latest.acquired_at.year
    ]
    if len(same_month) < MIN_SEASONAL_SAMPLES:
        return SeasonalCheck(latest, len(same_month))
    q1, _, q3 = quantiles(same_month, n=4, method="inclusive")
    position = "above" if latest.ndwi_median > q3 else "below" if latest.ndwi_median < q1 else "within"
    return SeasonalCheck(latest, len(same_month), position, q1, q3, median(same_month))


def interpret(observations: list[Obs], as_of: datetime | None = None) -> list[Note]:
    """Notes for one parcel. `as_of` limits the series to observations up to that time."""
    series = sorted(
        (o for o in observations if as_of is None or o.acquired_at <= as_of), key=lambda o: o.acquired_at
    )
    accepted = [o for o in series if o.status == "accepted" and o.ndwi_median is not None]
    if not accepted:
        return [Note("warning", "No accepted observation in the selected period.")]

    latest = accepted[-1]
    notes = [
        Note(
            "info",
            f"Latest accepted observation {latest.acquired_at:%Y-%m-%d}: median NDWI {latest.ndwi_median:.3f} "
            f"(p25–p75 {latest.ndwi_p25:.3f} to {latest.ndwi_p75:.3f}), {latest.valid_fraction:.0%} valid pixels.",
        )
    ]

    if len(accepted) > 1:
        previous = accepted[-2]
        delta = latest.ndwi_median - previous.ndwi_median
        direction = "higher (wetter)" if delta > 0 else "lower (drier)" if delta < 0 else "unchanged"
        notes.append(
            Note(
                "info",
                f"{abs(delta):.3f} {direction} than the previous accepted observation "
                f"({previous.acquired_at:%Y-%m-%d}, {(latest.acquired_at - previous.acquired_at).days} days earlier).",
            )
        )

    check = seasonal_check(accepted)
    month = f"{latest.acquired_at:%B}"
    if check.position:
        notes.append(
            Note(
                "info",
                f"{check.position.capitalize()} the typical {month} range of other years "
                f"(interquartile {check.q1:.3f} to {check.q3:.3f}, median {check.median:.3f}, n={check.n}).",
            )
        )
    else:
        notes.append(
            Note("warning", f"Only {check.n} accepted {month} observation(s) in other years: no seasonal comparison.")
        )

    newer_rejected = [o for o in series if o.acquired_at > latest.acquired_at and o.status == "rejected"]
    if newer_rejected:
        reasons = sorted({o.rejection_reason or "unknown" for o in newer_rejected})
        notes.append(
            Note(
                "warning",
                f"{len(newer_rejected)} newer scene(s) rejected ({', '.join(reasons)}); "
                f"the latest accepted value is {(series[-1].acquired_at - latest.acquired_at).days} days old.",
            )
        )

    notes.append(
        Note(
            "info",
            "Green/NIR NDWI reflects surface wetness and canopy state; it is not a calibrated soil-moisture "
            "measurement. Compare values from the same season only.",
        )
    )
    return notes
