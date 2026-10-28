"""Draw accepted NDWI medians per parcel over time as a standalone SVG.

    python scripts/plot_timeseries.py examples/runs/<run_id>/observations.csv examples/ndwi_timeseries.svg

No plotting dependency: the SVG is written by hand so the figure is
reproducible inside the slim Docker image.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

PARCELS = {  # parcel_id -> (label, colour)
    "wr-core-100m": ("Bog core 100 m", "#1f6feb"),
    "wr-bog-osm": ("Bog polygon (OSM)", "#2da44e"),
    "ref-field-100m": ("Reference field 100 m", "#d1242f"),
}
WIDTH, HEIGHT, LEFT, RIGHT, TOP, BOTTOM = 960, 420, 60, 20, 40, 50
Y_MIN, Y_MAX = -0.9, -0.3


def main(observations: Path, out: Path) -> None:
    series: dict[str, list[tuple[datetime, float]]] = {p: [] for p in PARCELS}
    with open(observations) as handle:
        for row in csv.DictReader(handle):
            if row["status"] == "accepted" and row["parcel_id"] in series:
                series[row["parcel_id"]].append((datetime.fromisoformat(row["acquired_at"]), float(row["ndwi_median"])))
    times = [t for points in series.values() for t, _ in points]
    t0, t1 = min(times).timestamp(), max(times).timestamp()

    def x(t: datetime) -> float:
        return LEFT + (t.timestamp() - t0) / (t1 - t0) * (WIDTH - LEFT - RIGHT)

    def y(v: float) -> float:
        v = min(max(v, Y_MIN), Y_MAX)
        return TOP + (Y_MAX - v) / (Y_MAX - Y_MIN) * (HEIGHT - TOP - BOTTOM)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" font-family="sans-serif" font-size="12">',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="white"/>',
        f'<text x="{LEFT}" y="22" font-size="14" font-weight="bold">Wurzacher Ried — Sentinel-2 NDWI (B03/B08), accepted parcel medians</text>',
    ]
    for tick in [-0.9, -0.8, -0.7, -0.6, -0.5, -0.4, -0.3]:
        parts.append(f'<line x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{y(tick):.1f}" y2="{y(tick):.1f}" stroke="#ddd"/>')
        parts.append(f'<text x="{LEFT - 8}" y="{y(tick) + 4:.1f}" text-anchor="end">{tick:.1f}</text>')
    for year in range(min(times).year, max(times).year + 1):
        tx = x(datetime(year, 1, 1, tzinfo=min(times).tzinfo))
        if LEFT <= tx <= WIDTH - RIGHT:
            parts.append(f'<line x1="{tx:.1f}" x2="{tx:.1f}" y1="{TOP}" y2="{HEIGHT - BOTTOM}" stroke="#eee"/>')
            parts.append(f'<text x="{tx + 4:.1f}" y="{HEIGHT - BOTTOM + 18}">{year}</text>')
    for i, (parcel_id, (label, colour)) in enumerate(PARCELS.items()):
        points = sorted(series[parcel_id])
        path = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in points)
        parts.append(f'<polyline points="{path}" fill="none" stroke="{colour}" stroke-width="1" opacity="0.5"/>')
        parts += [f'<circle cx="{x(t):.1f}" cy="{y(v):.1f}" r="2.2" fill="{colour}"/>' for t, v in points]
        lx = LEFT + 10 + i * 220
        parts.append(f'<circle cx="{lx}" cy="{HEIGHT - 12}" r="5" fill="{colour}"/>')
        parts.append(f'<text x="{lx + 10}" y="{HEIGHT - 8}">{label} (n={len(points)})</text>')
    parts.append("</svg>")
    out.write_text("\n".join(parts) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("observations", type=Path, help="observations.csv of a run")
    parser.add_argument("out", type=Path, help="SVG file to write")
    args = parser.parse_args()
    main(args.observations, args.out)
