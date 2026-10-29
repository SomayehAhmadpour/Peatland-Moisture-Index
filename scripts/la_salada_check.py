"""Evaluation: NDWI at La Salada lake (Argentina) on 12 April 2019 vs Santecchia et al. (2023).

The paper (J. Imaging 9, 186) reports McFeeters NDWI of the Sentinel-2B L2A pixels
on the lake shoreline, "all negative, between -0.64 and -0.01". This reads the
same acquisition from Earth Search in both processings (original baseline 02.11,
which the paper used, and the 05.00 reprocessing this pipeline uses), traces the
lake from NIR, and summarises NDWI on the shoreline and in open water.

    uv run --with scipy python scripts/la_salada_check.py

Writes examples/validation/la_salada_shoreline.csv.
"""

from __future__ import annotations

import argparse
import csv
import json
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform, transform_bounds
from rasterio.windows import from_bounds
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
SEARCH = "https://earth-search.aws.element84.com/v1/search"
BOUNDS = (-62.75, -39.50, -62.65, -39.43)  # west, south, east, north around the lake
LAKE_CENTRE = (-62.7005, -39.4625)  # lon, lat
ITEMS = {  # same acquisition, two processings
    "original (baseline 02.11)": ("sentinel-2-l2a", "S2B_20HNB_20190412_0_L2A"),
    "reprocessed (baseline 05.00)": ("sentinel-2-c1-l2a", "S2B_T20HNB_20190412T140835_L2A"),
}


def reflectance(collection: str, item_id: str) -> tuple[np.ndarray, np.ndarray, rasterio.Affine, str]:
    body = json.dumps({"collections": [collection], "ids": [item_id]}).encode()
    request = urllib.request.Request(SEARCH, body, {"Content-Type": "application/json"})
    assets = json.load(urllib.request.urlopen(request, timeout=60))["features"][0]["assets"]
    bands = []
    for key in ("green", "nir"):
        info = assets[key].get("raster:bands", [{}])[0]
        with rasterio.open(assets[key]["href"]) as src:
            window = from_bounds(*transform_bounds("EPSG:4326", src.crs, *BOUNDS), src.transform)
            window = window.round_offsets().round_lengths()
            dn = src.read(1, window=window).astype(float)
            grid, crs = src.window_transform(window), str(src.crs)
        bands.append(dn * info.get("scale", 1e-4) + info.get("offset", 0.0))
    return bands[0], bands[1], grid, crs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "examples" / "validation" / "la_salada_shoreline.csv")
    args = parser.parse_args()

    data = {name: reflectance(*item) for name, item in ITEMS.items()}
    green, nir, grid, crs = data["original (baseline 02.11)"]
    x, y = transform("EPSG:4326", crs, [LAKE_CENTRE[0]], [LAKE_CENTRE[1]])
    row, col = rasterio.transform.rowcol(grid, x[0], y[0])
    labels, _ = ndimage.label(nir < 0.07)  # open water is dark in NIR
    lake = labels == labels[row, col]
    groups = {
        "shoreline, both sides": (lake & ~ndimage.binary_erosion(lake)) | (ndimage.binary_dilation(lake) & ~lake),
        "shoreline, water side": lake & ~ndimage.binary_erosion(lake),
        "shoreline, land side": ndimage.binary_dilation(lake) & ~lake,
        "open water (>= 30 m from shore)": ndimage.binary_erosion(lake, iterations=3),
    }
    rows = []
    for processing, (green, nir, _, _) in data.items():
        with np.errstate(divide="ignore", invalid="ignore"):
            ndwi = (green - nir) / (green + nir)
        positive = (green > 0) & (nir > 0)
        for group, mask in groups.items():
            values = ndwi[mask & positive]
            rows.append({
                "processing": processing, "pixels": group, "n": int(mask.sum()),
                "n_reflectance_not_positive": int((mask & ~positive).sum()),
                "min": round(float(values.min()), 3), "p5": round(float(np.percentile(values, 5)), 3),
                "median": round(float(np.median(values)), 3), "p95": round(float(np.percentile(values, 95)), 3),
                "max": round(float(values.max()), 3), "share_negative": round(float(np.mean(values < 0)), 3),
            })  # fmt: skip
            print(rows[-1])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
