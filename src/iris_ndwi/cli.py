"""Command line entry point.

    iris-ndwi parcels --config config/wurzacher_ried.yaml
    iris-ndwi run     --config config/wurzacher_ried.yaml [--source stac|synthetic] [--to-db]
    iris-ndwi export  output/<run_id> [--schema my_ndwi] [--create-tables]   # URL from IRIS_STAGING_DATABASE_URL
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

from iris_ndwi.config import load_config
from iris_ndwi.outputs import write_outputs
from iris_ndwi.parcels import GeometryError, load_parcels
from iris_ndwi.pipeline import run_pipeline
from iris_ndwi.report import quality_summary
from iris_ndwi.sources import StacSource
from iris_ndwi.synthetic import SyntheticSource


def cmd_parcels(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    for parcel in load_parcels(config):
        print(
            f"{parcel.parcel_id:<14} {parcel.kind:<10} {parcel.area_m2 / 1e4:9.2f} ha  "
            f"valid={parcel.geometry.is_valid} repaired={parcel.geometry_repaired}  {parcel.source_ref}"
        )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.start:
        config = config.model_copy(update={"start_date": args.start})
    if args.end:
        config = config.model_copy(update={"end_date": args.end})
    config = type(config).model_validate(config.model_dump())  # re-validate overrides

    parcels = load_parcels(config)
    source = SyntheticSource() if args.source == "synthetic" else StacSource(config.stac_url, config.collections)
    result = run_pipeline(config, source, parcels)

    run_dir = write_outputs(result, args.output_dir or config.output_dir)
    (run_dir / "quality_summary.md").write_text(quality_summary(result))
    accepted = sum(o.status == "accepted" for o in result.observations)
    print(f"run {result.run_id}: {len(result.scenes)} scenes, {len(result.observations)} observations "
          f"({accepted} accepted) -> {run_dir}")  # fmt: skip

    if args.to_db:
        from iris_ndwi.staging import engine_from_env, write_run

        counts = write_run(result, engine_from_env())
        print(f"iris_staging upserted: {counts}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from sqlalchemy.exc import SQLAlchemyError

    from iris_ndwi.staging import DATABASE_URL_ENV, StagingGuardError, engine_for, export_run_dir

    url = os.environ.get(DATABASE_URL_ENV)
    if not url:
        print(f"set {DATABASE_URL_ENV} to the target database URL", file=sys.stderr)
        return 2
    try:
        engine = engine_for(url, args.schema)
        counts = export_run_dir(args.run_dir, engine, create_tables=args.create_tables)
    except (StagingGuardError, SQLAlchemyError) as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 2
    print(f"{engine.url.render_as_string(hide_password=True)} schema {args.schema} upserted: {counts}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="iris-ndwi", description="Sentinel-2 NDWI for peatland parcels")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    parcels = sub.add_parser("parcels", help="load and validate parcel geometries")
    parcels.add_argument("--config", type=Path, required=True)
    parcels.set_defaults(func=cmd_parcels)

    run = sub.add_parser("run", help="run the NDWI pipeline")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--source", choices=["stac", "synthetic"], default="stac")
    run.add_argument("--start", type=date.fromisoformat, help="override start_date (YYYY-MM-DD)")
    run.add_argument("--end", type=date.fromisoformat, help="override end_date (YYYY-MM-DD)")
    run.add_argument("--output-dir", type=Path)
    run.add_argument("--to-db", action="store_true", help="also upsert into iris_staging")
    run.set_defaults(func=cmd_run)

    export = sub.add_parser("export", help="export a run folder to a PostGIS database ($IRIS_STAGING_DATABASE_URL)")
    export.add_argument("run_dir", type=Path, help="e.g. output/<run_id>")
    export.add_argument("--schema", default="iris_staging", help="target schema (iris_core and public are refused)")
    export.add_argument("--create-tables", action="store_true", help="create PostGIS, the schema and the tables if missing")
    export.set_defaults(func=cmd_export)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        return args.func(args)
    except GeometryError as exc:
        print(f"geometry error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
