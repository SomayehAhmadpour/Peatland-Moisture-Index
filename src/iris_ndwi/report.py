"""Generate quality_summary.md for a run: coverage, rejections, comparability."""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median

from iris_ndwi.interpret import Obs, seasonal_check
from iris_ndwi.pipeline import RunResult

# Summer window used for year-to-year comparison (same phenological stage).
COMPARISON_MONTHS = (6, 7, 8)


def _fmt(value: float | None, digits: int = 3) -> str:
    return "–" if value is None else f"{value:.{digits}f}"


def _table(headers: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return lines


def _reason_group(reason: str) -> str:
    return reason.split(":", 1)[0]


def quality_summary(result: RunResult) -> str:
    cfg = result.config
    obs = result.observations
    lines = [
        f"# Quality summary — run `{result.run_id}`",
        "",
        f"- Source: **{result.source_name}**"
        + (" — **SYNTHETIC DATA, not real imagery**" if result.source_name == "synthetic" else f" ({cfg.stac_url}, collections in priority order: {', '.join(f'`{c}`' for c in cfg.collections)})"),
        f"- Study area: {cfg.study_area}; MGRS tile {cfg.mgrs_tile}; period {cfg.start_date} → {cfg.end_date}",
        f"- Thresholds: scene cloud ≤ {cfg.max_scene_cloud_pct:g} %, parcel valid fraction ≥ {cfg.min_valid_fraction:g}, "
        f"valid SCL classes {cfg.valid_scl_classes}, max blue reflectance {cfg.max_blue_reflectance}, "
        f"water threshold NDWI > {cfg.water_threshold:g}",
        f"- Config SHA-256: `{result.config_sha256}`",
        "",
        "## Scene ledger",
        "",
        f"{result.n_items_found} STAC items found, {len(result.scenes)} scenes processed, "
        f"{len(result.scene_rejections)} rejected.",
        "",
    ]
    reasons = Counter(_reason_group(r.reason) for r in result.scene_rejections)
    lines += _table(["Scene rejection reason", "Count"], [[k, v] for k, v in sorted(reasons.items())])

    baselines = Counter(
        (s.collection, s.processing_baseline, s.boa_offset_applied_flag, s.green.offset) for s in result.scenes
    )
    lines += ["", "Processed scenes by collection, processing baseline and applied BOA offset (reflectance units):", ""]
    lines += _table(
        ["Collection", "Baseline", "boa_offset_applied flag", "Offset applied by pipeline", "Scenes"],
        [[c, b, "–" if f is None else f, o, n] for (c, b, f, o), n in sorted(baselines.items(), key=str)],
    )

    lines += ["", "## Parcel observations", ""]
    rows = []
    for parcel in result.parcels:
        mine = [o for o in obs if o.parcel_id == parcel.parcel_id]
        accepted = [o for o in mine if o.status == "accepted"]
        rejected = Counter(o.rejection_reason for o in mine if o.status == "rejected")
        rows.append([
            parcel.parcel_id,
            f"{parcel.area_m2 / 1e4:.2f} ha",
            mine[0].n_pixels if mine else "–",
            len(mine),
            len(accepted),
            ", ".join(f"{k}: {v}" for k, v in sorted(rejected.items())) or "–",
            f"{accepted[0].acquired_at:%Y-%m-%d} → {accepted[-1].acquired_at:%Y-%m-%d}" if accepted else "–",
            _fmt(median(o.ndwi_median for o in accepted)) if accepted else "–",
        ])  # fmt: skip
    lines += _table(
        ["Parcel", "Area", "Pixels", "Observations", "Accepted", "Rejected (reason: n)", "Accepted range", "Median NDWI"],
        rows,
    )

    lines += [
        "",
        "## Monitoring: latest accepted value against the same month in other years",
        "",
        "Interquartile range of the parcel's accepted medians in the same calendar month of other years. "
        "**below** = drier than usual for the time of year, **above** = wetter; at least "
        "3 earlier values are needed. Green/NIR NDWI is a wetness indicator, not a calibrated soil moisture.",
        "",
    ]
    monitor_rows = []
    for parcel in result.parcels:
        accepted = sorted(
            (o for o in obs if o.parcel_id == parcel.parcel_id and o.status == "accepted"), key=lambda o: o.acquired_at
        )
        if not accepted:
            monitor_rows.append([parcel.parcel_id, "–", "–", "–", 0, "no accepted observation"])
            continue
        check = seasonal_check([
            Obs(o.acquired_at, o.status, o.ndwi_median, o.ndwi_p25, o.ndwi_p75, o.valid_fraction) for o in accepted
        ])  # fmt: skip
        monitor_rows.append([
            parcel.parcel_id,
            f"{check.latest.acquired_at:%Y-%m-%d}",
            _fmt(check.latest.ndwi_median),
            f"{_fmt(check.q1)} to {_fmt(check.q3)}" if check.position else "–",
            check.n,
            f"**{check.position}**" if check.position in ("above", "below") else check.position or "too few values",
        ])  # fmt: skip
    lines += _table(["Parcel", "Latest", "Median NDWI", "Usual range (same month)", "n", "Status"], monitor_rows)

    pixel_totals = Counter()
    for o in obs:
        pixel_totals.update({
            "nodata": o.n_nodata, "scl_masked": o.n_scl_masked, "bright_surface": o.n_bright_surface,
            "non_positive_reflectance": o.n_non_positive_reflectance,
            "zero_denominator": o.n_zero_denominator, "out_of_range": o.n_out_of_range, "valid": o.n_valid,
        })  # fmt: skip
    lines += ["", "## Pixel status totals (all parcels × all processed scenes)", ""]
    lines += _table(["Status", "Pixels"], [[k, v] for k, v in pixel_totals.items()])

    by_year: dict[tuple[str, int], list[float]] = defaultdict(list)
    for o in obs:
        if o.status == "accepted" and o.acquired_at.month in COMPARISON_MONTHS:
            by_year[(o.parcel_id, o.acquired_at.year)].append(o.ndwi_median)
    years = sorted({year for _, year in by_year})
    lines += [
        "",
        f"## Year-to-year comparison (accepted observations, months {list(COMPARISON_MONTHS)})",
        "",
        "Median of per-scene parcel medians; n = accepted scenes. Only same-season values are compared.",
        "",
    ]
    lines += _table(
        ["Parcel", *[str(y) for y in years]],
        [
            [p.parcel_id, *[
                f"{_fmt(median(v))} (n={len(v)})" if (v := by_year.get((p.parcel_id, y))) else "–" for y in years
            ]]
            for p in result.parcels
        ],
    )  # fmt: skip
    return "\n".join(lines) + "\n"
