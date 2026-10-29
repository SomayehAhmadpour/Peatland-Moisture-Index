"""Write a run to PostgreSQL/PostGIS: the isolated iris_staging schema, or a
schema of the user's own database (export).

Tables are declared once, in iris_staging; an export to another schema only
renames it via SQLAlchemy's schema_translate_map, so nothing is ever written
outside that one schema, and iris_core, public and the system schemas are
refused. Tables come from the Alembic migration in alembic/versions or, on
request, from create_tables (same tables, constraints and indexes). Writes
are idempotent upserts keyed on stable identifiers, so re-running a period
updates rows instead of duplicating them. Parcel geometries are stored in
EPSG:25832; runs in another metric CRS are reprojected.
"""

from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime
from pathlib import Path

from geoalchemy2 import Geometry
from geoalchemy2.shape import from_shape, to_shape
from shapely import wkb
from shapely.geometry import MultiPolygon, shape
from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Index, Integer, MetaData, String, Table, Text,
    UniqueConstraint, create_engine, func, make_url, text,
)  # fmt: skip
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine import Engine

from iris_ndwi.outputs import SCENE_COLUMNS, manifest, observation_rows, scene_rows
from iris_ndwi.parcels import reproject
from iris_ndwi.pipeline import RunResult, method_params_digest
from iris_ndwi.zonal import observation_id

STAGING_SCHEMA = "iris_staging"
DATABASE_URL_ENV = "IRIS_STAGING_DATABASE_URL"
GEOMETRY_CRS = "EPSG:25832"
PROTECTED_SCHEMAS = {"iris_core", "public", "information_schema"}
_SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

metadata = MetaData(schema=STAGING_SCHEMA)


def _audit() -> list[Column]:
    return [
        Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
        Column("updated_at", DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False),
    ]


ndwi_run = Table(
    "s2_ndwi_run", metadata,
    Column("run_id", String(64), primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("source", String(32), nullable=False),
    Column("synthetic_data", Boolean, nullable=False),
    Column("method_version", String(64), nullable=False),
    Column("config_sha256", String(64), nullable=False),
    Column("manifest", JSONB, nullable=False),
    *_audit(),
)  # fmt: skip

parcel = Table(
    "s2_ndwi_parcel", metadata,
    Column("parcel_id", String(64), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("source", String(64), nullable=False),
    Column("source_ref", String(255), nullable=False),
    Column("licence", String(255), nullable=False),
    Column("area_m2", Float, nullable=False),
    Column("geometry_repaired", Boolean, nullable=False),
    Column("geometry", Geometry("MULTIPOLYGON", srid=25832, spatial_index=False), nullable=False),
    *_audit(),
    CheckConstraint("ST_IsValid(geometry)", name="ck_s2_ndwi_parcel_valid_geometry"),
    Index("idx_s2_ndwi_parcel_geometry", "geometry", postgresql_using="gist"),
)  # fmt: skip

scene = Table(
    "s2_scene", metadata,
    Column("item_id", String(64), primary_key=True),
    Column("collection", String(64)),
    Column("acquired_at", DateTime(timezone=True)),
    Column("platform", String(32)),
    Column("mgrs_tile", String(8)),
    Column("processing_baseline", String(8)),
    Column("generation_time", String(40)),
    Column("scene_cloud_pct", Float),
    Column("boa_offset_applied_flag", Boolean),
    Column("boa_offset", Float),
    Column("reflectance_scale", Float),
    Column("sun_elevation", Float),
    Column("blue_href", Text),
    Column("green_href", Text),
    Column("nir_href", Text),
    Column("scl_href", Text),
    *_audit(),
    Index("idx_s2_scene_acquired_at", "acquired_at"),
)  # fmt: skip

# What each run decided about each item. Selection depends on the run's collections
# and pre-filter, so it is kept per run instead of being overwritten on s2_scene.
run_scene = Table(
    "s2_ndwi_run_scene", metadata,
    Column("run_id", String(64), ForeignKey(f"{STAGING_SCHEMA}.s2_ndwi_run.run_id"), primary_key=True),
    Column("item_id", String(64), ForeignKey(f"{STAGING_SCHEMA}.s2_scene.item_id"), primary_key=True),
    Column("selected", Boolean, nullable=False),
    Column("rejection_reason", Text),
    *_audit(),
    Index("idx_s2_ndwi_run_scene_item", "item_id"),
)  # fmt: skip

observation = Table(
    "s2_ndwi_observation", metadata,
    Column("observation_id", String(200), primary_key=True),
    Column("parcel_id", String(64), ForeignKey(f"{STAGING_SCHEMA}.s2_ndwi_parcel.parcel_id"), nullable=False),
    Column("item_id", String(64), ForeignKey(f"{STAGING_SCHEMA}.s2_scene.item_id"), nullable=False),
    Column("acquired_at", DateTime(timezone=True), nullable=False),
    Column("method_version", String(64), nullable=False),
    Column("method_params_sha256", String(64), nullable=False),
    Column("n_pixels", Integer, nullable=False),
    Column("n_nodata", Integer, nullable=False),
    Column("n_scl_masked", Integer, nullable=False),
    Column("n_bright_surface", Integer, nullable=False),
    Column("n_non_positive_reflectance", Integer, nullable=False),
    Column("n_zero_denominator", Integer, nullable=False),
    Column("n_out_of_range", Integer, nullable=False),
    Column("n_valid", Integer, nullable=False),
    Column("valid_fraction", Float, nullable=False),
    Column("ndwi_mean", Float),
    Column("ndwi_median", Float),
    Column("ndwi_std", Float),
    Column("ndwi_p25", Float),
    Column("ndwi_p75", Float),
    Column("ndwi_min", Float),
    Column("ndwi_max", Float),
    Column("water_fraction", Float),
    Column("status", String(16), nullable=False),
    Column("rejection_reason", String(64)),
    Column("run_id", String(64), ForeignKey(f"{STAGING_SCHEMA}.s2_ndwi_run.run_id"), nullable=False),
    *_audit(),
    UniqueConstraint(
        "parcel_id", "item_id", "method_version", "method_params_sha256", name="uq_s2_ndwi_observation_key"
    ),
    CheckConstraint("status IN ('accepted', 'rejected')", name="ck_s2_ndwi_observation_status"),
    CheckConstraint("valid_fraction BETWEEN 0 AND 1", name="ck_s2_ndwi_observation_fraction"),
    CheckConstraint("ndwi_median IS NULL OR ndwi_median BETWEEN -1 AND 1", name="ck_s2_ndwi_observation_range"),
    CheckConstraint(
        "n_pixels = n_valid + n_nodata + n_scl_masked + n_bright_surface + n_non_positive_reflectance"
        " + n_zero_denominator + n_out_of_range",
        name="ck_s2_ndwi_observation_counts",
    ),
    Index("idx_s2_ndwi_observation_parcel_time", "parcel_id", "acquired_at"),
)  # fmt: skip


class StagingGuardError(RuntimeError):
    """Refusal to write anywhere but the one target schema, or to overwrite a different parcel."""


def check_schema_name(schema: str) -> None:
    if not _SCHEMA_NAME.match(schema):
        raise StagingGuardError(f"{schema!r} is not a valid schema name (lower-case letters, digits, _)")
    if schema in PROTECTED_SCHEMAS or schema.startswith("pg_"):
        raise StagingGuardError(f"refusing to write to schema {schema}; choose a separate schema such as {STAGING_SCHEMA}")


def engine_for(url: str, schema: str = STAGING_SCHEMA) -> Engine:
    """Engine whose tables live in schema (default iris_staging). postgresql:// URLs use psycopg 3."""
    check_schema_name(schema)
    parsed = make_url(url)
    if parsed.drivername in ("postgresql", "postgres"):
        parsed = parsed.set(drivername="postgresql+psycopg")
    # public stays on the path for the PostGIS types (geometry); every table name is schema-qualified
    engine = create_engine(parsed, connect_args={"options": f"-csearch_path={schema},public"})
    return engine.execution_options(schema_translate_map={STAGING_SCHEMA: schema})


def engine_from_env() -> Engine:
    url = os.environ.get(DATABASE_URL_ENV)
    if not url:
        raise StagingGuardError(f"set {DATABASE_URL_ENV} to the staging database URL")
    return engine_for(url)


def target_schema(engine: Engine) -> str:
    return (engine.get_execution_options().get("schema_translate_map") or {}).get(STAGING_SCHEMA, STAGING_SCHEMA)


def prepare(engine: Engine, create_tables: bool = False) -> None:
    """Fail unless every table we write lives in the target schema and that schema exists.

    With create_tables, a missing PostGIS extension, schema or table is created instead.
    """
    schema = target_schema(engine)
    check_schema_name(schema)
    for table in metadata.tables.values():
        if table.schema != STAGING_SCHEMA:
            raise StagingGuardError(f"table {table.fullname} is outside {STAGING_SCHEMA}")
    with engine.begin() as connection:
        if not connection.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'postgis'")).scalar():
            if not create_tables:
                raise StagingGuardError("the PostGIS extension is not installed in this database")
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        exists = connection.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"), {"s": schema}
        ).scalar()
        if not exists:
            if not create_tables:
                raise StagingGuardError(
                    f"schema {schema} does not exist; run `alembic upgrade head` first or let the export create the tables"
                )
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        if create_tables:
            metadata.create_all(connection, checkfirst=True)


def _upsert(connection, table: Table, rows: list[dict], keep_known: bool = False) -> None:
    """Insert or update by primary key. keep_known: a NULL never replaces a stored value."""
    if not rows:
        return
    key = [c.name for c in table.primary_key.columns]
    statement = insert(table).values(rows)
    updates = {
        c.name: func.coalesce(statement.excluded[c.name], c) if keep_known else statement.excluded[c.name]
        for c in table.columns
        if c.name not in key and c.name not in ("created_at", "updated_at")
    }
    updates["updated_at"] = func.now()
    connection.execute(statement.on_conflict_do_update(index_elements=key, set_=updates))


def _check_parcels(connection, schema: str, parcel_rows: list[dict]) -> None:
    """A parcel_id that already exists must keep its geometry: older observations refer to it."""
    new = {row["parcel_id"]: to_shape(row["geometry"]) for row in parcel_rows}
    existing = connection.execute(
        text(f'SELECT parcel_id, ST_AsBinary(geometry) FROM "{schema}".s2_ndwi_parcel WHERE parcel_id = ANY(:ids)'),
        {"ids": list(new)},
    )
    clashes = [pid for pid, old in existing if wkb.loads(bytes(old)).hausdorff_distance(new[pid]) > 0.5]
    if clashes:
        raise StagingGuardError(
            f"parcel {', '.join(sorted(clashes))} already exists in {schema} with a different geometry; "
            "export into another schema or remove the old parcel first"
        )


def _parcel_row(properties: dict, geometry, crs: str) -> dict:
    geometry = reproject(geometry, crs, GEOMETRY_CRS)
    if geometry.geom_type == "Polygon":
        geometry = MultiPolygon([geometry])
    return {
        **{k: properties[k] for k in ("parcel_id", "name", "kind", "source", "source_ref", "licence", "area_m2")},
        "geometry_repaired": bool(properties["geometry_repaired"]),  # area_m2 stays as measured in the run's CRS
        "geometry": from_shape(geometry, srid=25832),
    }


def _run_row(run_manifest: dict) -> dict:
    finished = run_manifest["finished_at"]
    return {
        "run_id": run_manifest["run_id"],
        "started_at": datetime.fromisoformat(run_manifest["started_at"]),
        "finished_at": datetime.fromisoformat(finished) if finished else None,
        "source": run_manifest["source"],
        "synthetic_data": run_manifest["synthetic_data"],
        "method_version": run_manifest["method_version"],
        "config_sha256": run_manifest["config_sha256"],
        "manifest": run_manifest,
    }


def _write(engine: Engine, run_row: dict, parcel_rows: list[dict], scenes: list[dict], observations: list[dict],
           create_tables: bool) -> dict[str, int]:  # fmt: skip
    """scenes: ledger rows (item metadata plus this run's selected/rejection_reason)."""
    metadata_rows = [{k: v for k, v in row.items() if k in scene.c} for row in scenes]
    decisions = [
        {"run_id": run_row["run_id"], "item_id": row["item_id"], "selected": row["selected"],
         "rejection_reason": row["rejection_reason"]}
        for row in scenes
    ]  # fmt: skip
    prepare(engine, create_tables)
    with engine.begin() as connection:  # one transaction: all or nothing
        _check_parcels(connection, target_schema(engine), parcel_rows)
        _upsert(connection, ndwi_run, [run_row])
        _upsert(connection, parcel, parcel_rows)
        for chunk in range(0, len(scenes), 500):
            # another run may know more about an item (e.g. it rejected it before parsing); keep that
            _upsert(connection, scene, metadata_rows[chunk : chunk + 500], keep_known=True)
            _upsert(connection, run_scene, decisions[chunk : chunk + 500])
        for chunk in range(0, len(observations), 500):
            _upsert(connection, observation, observations[chunk : chunk + 500])
    return {"runs": 1, "parcels": len(parcel_rows), "scenes": len(scenes), "observations": len(observations)}


def write_run(result: RunResult, engine: Engine, create_tables: bool = False) -> dict[str, int]:
    crs = result.config.target_crs
    parcel_rows = [_parcel_row(vars(p) | {"area_m2": p.area_m2}, p.geometry, crs) for p in result.parcels]
    scenes = [
        {
            **{column: row.get(column) for column in SCENE_COLUMNS},
            "acquired_at": datetime.fromisoformat(row["acquired_at"]) if row["acquired_at"] else None,
        }
        for row in scene_rows(result)
    ]
    observations = [
        {**row, "acquired_at": datetime.fromisoformat(row["acquired_at"]), "run_id": result.run_id}
        for row in observation_rows(result)
    ]
    return _write(engine, _run_row(manifest(result)), parcel_rows, scenes, observations, create_tables)


def _typed(table: Table, row: dict[str, str]) -> dict:
    """A CSV row (all strings, "" for missing) with the column types of table."""
    out = {}
    for column in table.columns:
        if column.name not in row:
            continue
        value = row[column.name]
        if value == "":
            value = None
        elif isinstance(column.type, Boolean):
            value = value == "True"
        elif isinstance(column.type, Integer):
            value = int(value)
        elif isinstance(column.type, Float):
            value = float(value)
        elif isinstance(column.type, DateTime):
            value = datetime.fromisoformat(value)
        out[column.name] = value
    return out


def run_dir_rows(run_dir: Path) -> tuple[dict, list[dict], list[dict], list[dict]]:
    """Run, parcel, scene and observation rows from a run folder written by outputs.write_outputs."""
    run_dir = Path(run_dir)
    run_manifest = json.loads((run_dir / "manifest.json").read_text())
    collection = json.loads((run_dir / "parcels.geojson").read_text())
    crs_name = collection.get("crs", {}).get("properties", {}).get("name")
    crs = f"EPSG:{crs_name.rsplit(':', 1)[1]}" if crs_name else run_manifest["config"]["target_crs"]
    parcel_rows = [_parcel_row(f["properties"], shape(f["geometry"]), crs) for f in collection["features"]]

    def csv_rows(name: str, *tables: Table) -> list[dict]:
        with open(run_dir / name, newline="") as handle:
            return [
                {k: v for table in tables for k, v in _typed(table, row).items()} | {"run_id": run_manifest["run_id"]}
                for row in csv.DictReader(handle)
            ]

    scenes = csv_rows("scenes.csv", scene, run_scene)
    for row in scenes:  # run folders written before blue_href was recorded
        row.setdefault("blue_href", None)
    # Run folders written before the thresholds were part of the key: derive it from the manifest.
    params = run_manifest.get("method_params_sha256") or method_params_digest(run_manifest["config"])
    observations = [
        row | {
            "method_params_sha256": params,
            "observation_id": observation_id(row["parcel_id"], row["item_id"], row["method_version"], params),
        }
        for row in csv_rows("observations.csv", observation)
    ]  # fmt: skip
    return _run_row(run_manifest), parcel_rows, scenes, observations


def export_run_dir(run_dir: Path, engine: Engine, create_tables: bool = False) -> dict[str, int]:
    """Upsert a finished run folder (output/<run_id>) into the engine's schema."""
    return _write(engine, *run_dir_rows(run_dir), create_tables)
