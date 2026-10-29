"""Evaluation: compare pipeline runs with the JNCC Sentinel-2 ARD NDWI v2 product.

JNCC publishes (B03 - B08) / (B03 + B08) for every Sentinel-2 acquisition over
England and Scotland (Defra/JNCC surface reflectance, 10 m, EPSG:27700, open
data on CEDA). For each date a run accepted, this reads the JNCC file of the
same day and takes the median of its pixels inside the run's box. JNCC v2 files
carry no cloud mask, so only dates our SCL mask accepted are compared.

    uv run --group ui --with matplotlib python scripts/jncc_check.py \
        examples/runs/<thorne_run> examples/runs/<hornsea_run> --plot examples/validation/jncc_comparison.png

Writes examples/validation/jncc_<site>.csv per run and jncc_summary.csv.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from statistics import mean, median

import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.warp import transform_geom
from rasterio.windows import from_bounds
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parent.parent
LISTING = "https://data.ceda.ac.uk/neodc/sentinel_ard/products/indices/sentinel_2/ndwi/v2/{day:%Y/%m/%d}?json"
DOWNLOAD = "https://dap.ceda.ac.uk"
NODATA = -9999


def jncc_median(day: date, box: dict, tile: str) -> tuple[float | None, str]:
    """Median JNCC NDWI inside the box (EPSG:27700) on that day, and the file it came from."""
    try:
        items = json.load(urllib.request.urlopen(LISTING.format(day=day), timeout=60))["items"]
    except OSError as exc:
        return None, f"listing failed: {exc}"
    files = sorted((i for i in items if i["name"].endswith("_NDWI.tif")), key=lambda i: f"_T{tile}_" not in i["name"])
    left, bottom, right, top = shape(box).bounds
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="3", GDAL_HTTP_RETRY_DELAY="2"):
        for item in files:  # granules overlap; take the first one that covers the box with data
            try:
                with rasterio.open("/vsicurl/" + DOWNLOAD + item["path"]) as src:
                    b = src.bounds
                    if not (b.left <= left and b.right >= right and b.bottom <= bottom and b.top >= top):
                        continue
                    window = from_bounds(left, bottom, right, top, src.transform)
                    window = window.round_offsets(op="floor").round_lengths(op="ceil")
                    values = src.read(1, window=window, boundless=True, fill_value=NODATA)
                    inside = ~geometry_mask([box], values.shape, src.window_transform(window))
                    valid = values[inside & (values != NODATA) & np.isfinite(values)]
            except rasterio.RasterioIOError:
                continue
            if valid.size and valid.size >= 0.8 * inside.sum():
                return float(np.median(valid)), item["name"]
    return None, "no JNCC file with data over the box"


def compare_run(run_dir: Path) -> tuple[str, list[dict]]:
    collection = json.loads((run_dir / "parcels.geojson").read_text())
    crs = "EPSG:" + collection["crs"]["properties"]["name"].rsplit(":", 1)[-1]
    feature = collection["features"][0]
    box = transform_geom(crs, "EPSG:27700", feature["geometry"])
    with (run_dir / "scenes.csv").open() as f:
        tile = max(set(tiles := [r["mgrs_tile"] for r in csv.DictReader(f)]), key=tiles.count)
    with (run_dir / "observations.csv").open() as f:
        accepted = [r for r in csv.DictReader(f) if r["status"] == "accepted"]
    days = [datetime.fromisoformat(r["acquired_at"]).date() for r in accepted]
    with ThreadPoolExecutor(8) as pool:
        jncc = list(pool.map(lambda d: jncc_median(d, box, tile), days))
    rows = []
    for obs, day, (value, source) in zip(accepted, days, jncc):
        ours = float(obs["ndwi_median"])
        rows.append({
            "date": day.isoformat(), "item_id": obs["item_id"], "ours_ndwi_median": round(ours, 4),
            "jncc_ndwi_median": None if value is None else round(value, 4),
            "difference": None if value is None else round(ours - value, 4), "jncc_file": source,
        })  # fmt: skip
    return feature["properties"]["name"], rows


def summary(name: str, rows: list[dict]) -> dict:
    pairs = [(r["ours_ndwi_median"], r["jncc_ndwi_median"]) for r in rows if r["jncc_ndwi_median"] is not None]
    diffs = [a - b for a, b in pairs]
    return {
        "site": name, "dates_accepted": len(rows), "dates_compared": len(pairs),
        "mean_difference": round(mean(diffs), 3), "median_difference": round(median(diffs), 3),
        "mae": round(mean(abs(d) for d in diffs), 3), "rmse": round(math.sqrt(mean(d * d for d in diffs)), 3),
        "pearson_r": round(float(np.corrcoef(pairs, rowvar=False)[0, 1]), 3),
        "within_0_05": round(sum(abs(d) <= 0.05 for d in diffs) / len(diffs), 3),
        "within_0_10": round(sum(abs(d) <= 0.10 for d in diffs) / len(diffs), 3),
    }  # fmt: skip


def plot(sites: list[tuple[dict, list[dict]]], out: Path) -> None:
    import matplotlib  # optional: only needed for --plot

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(len(sites), 2, figsize=(15, 4.3 * len(sites)), squeeze=False,
                             gridspec_kw={"width_ratios": [3.2, 1]})  # fmt: skip
    for (series_ax, scatter_ax), (stats, rows) in zip(axes, sites):
        rows = [r for r in rows if r["jncc_ndwi_median"] is not None]
        days = [date.fromisoformat(r["date"]) for r in rows]
        ours, jncc = [r["ours_ndwi_median"] for r in rows], [r["jncc_ndwi_median"] for r in rows]
        series_ax.plot(days, ours, "-o", ms=3.5, lw=1.2, color="#2563EB", label="This pipeline (Sen2Cor L2A, Earth Search)")
        series_ax.plot(days, jncc, "-o", ms=3.5, lw=1.2, color="#EA580C", label="JNCC Sentinel-2 ARD NDWI v2")
        series_ax.axhline(0, color="#94A3B8", lw=0.8)
        series_ax.set_title(stats["site"], loc="left", fontsize=12, fontweight="bold")
        series_ax.set_ylabel("NDWI (B03−B08)/(B03+B08)")
        series_ax.legend(loc="lower left", fontsize=9, frameon=False)
        series_ax.grid(alpha=0.25)
        low, high = min(ours + jncc) - 0.05, max(ours + jncc) + 0.05
        scatter_ax.plot([low, high], [low, high], color="#94A3B8", lw=1)
        scatter_ax.scatter(jncc, ours, s=12, color="#2563EB", alpha=0.7)
        scatter_ax.set(xlim=(low, high), ylim=(low, high), aspect="equal", xlabel="JNCC NDWI", ylabel="This pipeline NDWI")
        scatter_ax.grid(alpha=0.25)
        scatter_ax.text(0.03, 0.97, f"n = {stats['dates_compared']} dates\nbias {stats['mean_difference']:+.3f}\n"
                        f"RMSE {stats['rmse']:.3f}\nr = {stats['pearson_r']:.2f}", transform=scatter_ax.transAxes,
                        va="top", fontsize=9, bbox={"fc": "white", "ec": "#E5E7EB"})  # fmt: skip
    fig.suptitle("Same 100 m box, same Sentinel-2 dates: this pipeline vs JNCC NDWI", fontsize=13)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=110)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", type=Path, nargs="+", help="run folders of 100 m boxes in England or Scotland")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "examples" / "validation")
    parser.add_argument("--plot", type=Path, help="write a PNG chart (needs matplotlib)")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    sites = []
    for run_dir in args.runs:
        name, rows = compare_run(run_dir)
        slug = re.sub(r"[^a-z0-9]+", "_", name.split("(")[0].strip().lower()).strip("_")
        with (args.out_dir / f"jncc_{slug}.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        sites.append((summary(name, rows), rows))
        print(json.dumps(sites[-1][0]))
    with (args.out_dir / "jncc_summary.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(sites[0][0]))
        writer.writeheader()
        writer.writerows(stats for stats, _ in sites)
    if args.plot:
        plot(sites, args.plot)


if __name__ == "__main__":
    main()
