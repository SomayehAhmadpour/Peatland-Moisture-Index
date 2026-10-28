"""Write a run to the isolated iris_staging schema (PostgreSQL/PostGIS).

This module never writes outside STAGING_SCHEMA. Tables are created by the
Alembic migration in alembic/versions; writes are idempotent upserts keyed on
stable identifiers, so re-running a period updates rows instead of
duplicating them.
"""

from __future__ import annotations

import os
from datetime import datetime

from geoalchemy2 import Geometry
from geoalchemy2.shape import from_shape
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, MetaData, String, Table, Text,
    create_engine, func, text,
)  # fmt: skip
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine import Engine

from iris_ndwi.outputs import SCENE_COLUMNS, manifest, observation_rows, scene_rows
from iris_ndwi.pipeline import RunResult

STAGING_SCHEMA = "iris_staging"
DATABASE_URL_ENV = "IRIS_STAGING_DATABASE_URL"

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
    Column("selected", Boolean, nullable=False),
    Column("rejection_reason", Text),
    Column("green_href", Text),
    Column("nir_href", Text),
    Column("scl_href", Text),
    Column("run_id", String(64), ForeignKey(f"{STAGING_SCHEMA}.s2_ndwi_run.run_id"), nullable=False),
    *_audit(),
)  # fmt: skip

observation = Table(
    "s2_ndwi_observation", metadata,
    Column("observation_id", String(200), primary_key=True),
    Column("parcel_id", String(64), ForeignKey(f"{STAGING_SCHEMA}.s2_ndwi_parcel.parcel_id"), nullable=False),
    Column("item_id", String(64), ForeignKey(f"{STAGING_SCHEMA}.s2_scene.item_id"), nullable=False),
    Column("acquired_at", DateTime(timezone=True), nullable=False),
    Column("method_version", String(64), nullable=False),
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
)  # fmt: skip


class StagingGuardError(RuntimeError):
    """Refusal to write anywhere but the staging schema."""


def assert_staging_only(engine: Engine) -> None:
    """Fail unless every table we write lives in iris_staging and the schema exists."""
    for table in metadata.tables.values():
        if table.schema != STAGING_SCHEMA:
            raise StagingGuardError(f"table {table.fullname} is outside {STAGING_SCHEMA}")
    with engine.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"), {"s": STAGING_SCHEMA}
        ).scalar()
    if not exists:
        raise StagingGuardError(f"schema {STAGING_SCHEMA} does not exist; run `alembic upgrade head` first")


def engine_from_env() -> Engine:
    url = os.environ.get(DATABASE_URL_ENV)
    if not url:
        raise StagingGuardError(f"set {DATABASE_URL_ENV} to the staging database URL")
    # public stays on the path for the PostGIS types (geometry); every table name is schema-qualified
    return create_engine(url, connect_args={"options": f"-csearch_path={STAGING_SCHEMA},public"})


def _upsert(connection, table: Table, rows: list[dict]) -> None:
    if not rows:
        return
    key = [c.name for c in table.primary_key.columns]
    statement = insert(table).values(rows)
    updates = {c.name: statement.excluded[c.name] for c in table.columns if c.name not in key and c.name != "created_at"}
    updates["updated_at"] = func.now()
    connection.execute(statement.on_conflict_do_update(index_elements=key, set_=updates))


def write_run(result: RunResult, engine: Engine) -> dict[str, int]:
    assert_staging_only(engine)
    run_manifest = manifest(result)
    run_row = {
        "run_id": result.run_id,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "source": result.source_name,
        "synthetic_data": run_manifest["synthetic_data"],
        "method_version": run_manifest["method_version"],
        "config_sha256": result.config_sha256,
        "manifest": run_manifest,
    }
    parcel_rows = [
        {
            "parcel_id": p.parcel_id, "name": p.name, "kind": p.kind, "source": p.source,
            "source_ref": p.source_ref, "licence": p.licence, "area_m2": p.area_m2,
            "geometry_repaired": p.geometry_repaired, "geometry": from_shape(p.geometry, srid=25832),
        }
        for p in result.parcels
    ]  # fmt: skip
    scenes = [
        {
            **{column: row.get(column) for column in SCENE_COLUMNS},
            "acquired_at": datetime.fromisoformat(row["acquired_at"]) if row["acquired_at"] else None,
            "run_id": result.run_id,
        }
        for row in scene_rows(result)
    ]
    observations = [
        {**row, "acquired_at": datetime.fromisoformat(row["acquired_at"]), "run_id": result.run_id}
        for row in observation_rows(result)
    ]
    with engine.begin() as connection:  # one transaction: all or nothing
        _upsert(connection, ndwi_run, [run_row])
        _upsert(connection, parcel, parcel_rows)
        for chunk in range(0, len(scenes), 500):
            _upsert(connection, scene, scenes[chunk : chunk + 500])
        for chunk in range(0, len(observations), 500):
            _upsert(connection, observation, observations[chunk : chunk + 500])
    return {"runs": 1, "parcels": len(parcel_rows), "scenes": len(scenes), "observations": len(observations)}
