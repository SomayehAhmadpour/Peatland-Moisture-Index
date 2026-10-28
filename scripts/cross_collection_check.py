"""Validation: does the legacy collection, with our offset handling, reproduce
Collection-1 NDWI for the same acquisitions?

Runs the pipeline once per collection over the same period and joins the
accepted parcel observations on (parcel, platform, UTC date). Writes
examples/cross_collection_check.csv and prints summary statistics.

    python scripts/cross_collection_check.py --start 2023-04-01 --end 2023-10-31
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path
from statistics import median

from iris_ndwi.config import load_config
from iris_ndwi.parcels import load_parcels
from iris_ndwi.pipeline import run_pipeline
from iris_ndwi.sources import StacSource

ROOT = Path(__file__).resolve().parent.parent


def accepted_by_key(config, collection: str) -> dict[tuple, tuple]:
    cfg = config.model_copy(update={"collections": [collection]})
    result = run_pipeline(cfg, StacSource(cfg.stac_url, cfg.collections), load_parcels(cfg))
    platforms = {s.item_id: s.platform for s in result.scenes}
    return {
        (o.parcel_id, platforms[o.item_id], o.acquired_at.date().isoformat()): (o.item_id, o.ndwi_median)
        for o in result.observations
        if o.status == "accepted"
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "wurzacher_ried.yaml")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2023, 4, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2023, 10, 31))
    parser.add_argument("--out", type=Path, default=ROOT / "examples" / "cross_collection_check.csv")
    args = parser.parse_args()

    # The 100 m boxes are enough for a per-acquisition comparison and keep the
    # COG reads small (the reserve polygons need a ~7 x 6 km window per scene).
    config = load_config(args.config).model_copy(
        update={"start_date": args.start, "end_date": args.end, "parcels_geojson": None}
    )
    c1 = accepted_by_key(config, "sentinel-2-c1-l2a")
    legacy = accepted_by_key(config, "sentinel-2-l2a")

    rows = []
    for key in sorted(c1.keys() & legacy.keys()):
        (c1_id, c1_value), (legacy_id, legacy_value) = c1[key], legacy[key]
        rows.append({
            "parcel_id": key[0], "platform": key[1], "date": key[2],
            "c1_item_id": c1_id, "legacy_item_id": legacy_id,
            "c1_ndwi_median": c1_value, "legacy_ndwi_median": legacy_value,
            "difference": round(legacy_value - c1_value, 6),
        })  # fmt: skip

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["parcel_id"])
        writer.writeheader()
        writer.writerows(rows)

    differences = [abs(r["difference"]) for r in rows]
    print(f"{len(rows)} matched parcel observations ({args.start} - {args.end}) -> {args.out}")
    if differences:
        print(f"|legacy - C1| NDWI median: median {median(differences):.4f}, max {max(differences):.4f}")


if __name__ == "__main__":
    main()
