"""iris_staging writer. Unit checks run always; the PostGIS round trip needs
IRIS_TEST_DATABASE_URL (provided by `docker compose run --rm tests`)."""

import csv
import json
import os
import shutil
import uuid
from datetime import date, datetime

import pytest
from geoalchemy2.shape import to_shape
from pyproj import Transformer
from sqlalchemy import create_engine, text

from iris_ndwi.adhoc import point_config, run_and_write
from iris_ndwi.staging import (
    STAGING_SCHEMA, StagingGuardError, check_schema_name, engine_for, engine_from_env, export_run_dir, metadata,
    run_dir_rows, target_schema, write_run,
)  # fmt: skip
from iris_ndwi.synthetic import SyntheticSource


def test_all_tables_live_in_staging_schema():
    assert metadata.tables
    assert {t.schema for t in metadata.tables.values()} == {STAGING_SCHEMA}
    assert not any("iris_core" in name for name in metadata.tables)


def test_missing_database_url_is_an_error(monkeypatch):
    monkeypatch.delenv("IRIS_STAGING_DATABASE_URL", raising=False)
    with pytest.raises(StagingGuardError, match="IRIS_STAGING_DATABASE_URL"):
        engine_from_env()


@pytest.mark.parametrize("schema", ["iris_core", "public", "pg_catalog", "information_schema", "Bad-Name", "x; drop"])
def test_protected_or_invalid_schemas_are_refused(schema):
    with pytest.raises(StagingGuardError):
        check_schema_name(schema)


def test_engine_for_targets_the_chosen_schema():
    engine = engine_for("postgresql://u:secret@db.example:5432/gis", "my_ndwi")
    assert engine.url.drivername == "postgresql+psycopg"  # psycopg 3, the driver that is installed
    assert target_schema(engine) == "my_ndwi"
    assert target_schema(create_engine("postgresql+psycopg://u@h/d")) == STAGING_SCHEMA


def test_run_dir_rows_are_typed(config, tmp_path):
    result, run_dir = run_and_write(config.model_copy(update={"start_date": date(2025, 5, 1)}), tmp_path, SyntheticSource())
    run, parcels, scenes, observations = run_dir_rows(run_dir)
    assert run["run_id"] == result.run_id and isinstance(run["started_at"], datetime)
    assert len(parcels) == len(result.parcels) and len(observations) == len(result.observations)
    assert len(scenes) == len(result.scenes) + len(result.scene_rejections)
    row = observations[0]
    assert isinstance(row["n_pixels"], int) and isinstance(row["valid_fraction"], float)
    assert isinstance(row["acquired_at"], datetime) and row["run_id"] == result.run_id
    assert all(isinstance(r["selected"], bool) for r in scenes)
    assert any(r["ndwi_median"] is None for r in observations)  # rejected: empty cell -> NULL
    assert {r["observation_id"] for r in observations} == {o.observation_id for o in result.observations}


def test_run_dir_rows_derive_the_threshold_digest_for_older_run_folders(config, tmp_path):
    """Run folders written before method_params_sha256 existed get the same key a new run would."""
    result, run_dir = run_and_write(config.model_copy(update={"start_date": date(2025, 5, 1)}), tmp_path, SyntheticSource())
    manifest = json.loads((run_dir / "manifest.json").read_text())
    del manifest["method_params_sha256"]
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    with open(run_dir / "observations.csv", newline="") as handle:
        rows = list(csv.DictReader(handle))
    old_columns = [c for c in rows[0] if c != "method_params_sha256"]
    with open(run_dir / "observations.csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, old_columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({**r, "observation_id": r["observation_id"].rsplit(":", 1)[0]} for r in rows)
    observations = run_dir_rows(run_dir)[3]
    assert {r["observation_id"] for r in observations} == {o.observation_id for o in result.observations}
    assert {r["method_params_sha256"] for r in observations} == {result.method_params_sha256}


def test_run_dir_rows_reproject_other_utm_zones_to_25832(config, tmp_path):
    result, run_dir = run_and_write(
        point_config(config, 47.926242, 9.877493, 100, date(2021, 1, 1), date(2026, 1, 1), "Box"), tmp_path, SyntheticSource()
    )
    # the same box as if the run had used the neighbouring zone (EPSG:25833)
    to_33 = Transformer.from_crs("EPSG:25832", "EPSG:25833", always_xy=True)
    collection = json.loads((run_dir / "parcels.geojson").read_text())
    ring = collection["features"][0]["geometry"]["coordinates"][0][0]
    collection["features"][0]["geometry"]["coordinates"][0][0] = [list(to_33.transform(x, y)) for x, y in ring]
    collection["crs"]["properties"]["name"] = "urn:ogc:def:crs:EPSG::25833"
    (run_dir / "parcels.geojson").write_text(json.dumps(collection))
    (row,) = run_dir_rows(run_dir)[1]
    assert to_shape(row["geometry"]).hausdorff_distance(result.parcels[0].geometry) < 0.01


@pytest.mark.integration
def test_export_run_dir_creates_tables_in_its_own_schema(config, tmp_path):
    url = os.environ.get("IRIS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("IRIS_TEST_DATABASE_URL not set")
    schema = f"iris_export_test_{uuid.uuid4().hex[:8]}"
    _, run_dir = run_and_write(
        point_config(config, 47.926242, 9.877493, 100, date(2021, 1, 1), date(2026, 1, 1), "Box"), tmp_path, SyntheticSource()
    )
    engine = engine_for(url, schema)
    try:
        with pytest.raises(StagingGuardError, match="does not exist"):
            export_run_dir(run_dir, engine)  # nothing is created unless asked
        first = export_run_dir(run_dir, engine, create_tables=True)
        assert export_run_dir(run_dir, engine) == first  # idempotent
        with engine.connect() as connection:
            n = connection.execute(text(f"SELECT count(*) FROM {schema}.s2_ndwi_observation")).scalar()
            srid, valid = connection.execute(
                text(f"SELECT ST_SRID(geometry), ST_IsValid(geometry) FROM {schema}.s2_ndwi_parcel")
            ).one()
            staging = connection.execute(  # nothing leaked into the staging schema
                text(f"SELECT count(*) FROM information_schema.tables WHERE table_schema = '{schema}'")
            ).scalar()
        assert n == first["observations"] > 0 and (srid, valid) == (25832, True) and staging == 5

        moved = tmp_path / "moved"
        shutil.copytree(run_dir, moved)
        collection = json.loads((moved / "parcels.geojson").read_text())
        ring = collection["features"][0]["geometry"]["coordinates"][0][0]
        collection["features"][0]["geometry"]["coordinates"][0][0] = [[x + 50, y] for x, y in ring]
        (moved / "parcels.geojson").write_text(json.dumps(collection))
        with pytest.raises(StagingGuardError, match="different geometry"):
            export_run_dir(moved, engine)
    finally:
        with engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


@pytest.mark.integration
def test_round_trip_and_idempotent_upsert(boxes_only_config):
    url = os.environ.get("IRIS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("IRIS_TEST_DATABASE_URL not set")
    from iris_ndwi.parcels import load_parcels
    from iris_ndwi.pipeline import run_pipeline
    from iris_ndwi.synthetic import SyntheticSource

    engine = create_engine(url)
    result = run_pipeline(boxes_only_config, SyntheticSource(), load_parcels(boxes_only_config))
    stricter_config = boxes_only_config.model_copy(update={"max_blue_reflectance": 0.10, "max_scene_cloud_pct": 30})
    stricter = run_pipeline(stricter_config, SyntheticSource(), load_parcels(stricter_config))
    try:
        first = write_run(result, engine)
        second = write_run(result, engine)  # same ids -> update, not duplicate
        assert first == second
        check_round_trip(engine, result)

        # Other thresholds: rows are added next to the first run's, which stay untouched,
        # and each run keeps its own scene decisions.
        write_run(stricter, engine)
        check_round_trip(engine, result)
        check_round_trip(engine, stricter)
        with engine.connect() as connection:
            decisions = dict(connection.execute(
                text(f"SELECT run_id, count(*) FILTER (WHERE selected) FROM {STAGING_SCHEMA}.s2_ndwi_run_scene "
                     "WHERE run_id IN (:a, :b) GROUP BY run_id"),
                {"a": result.run_id, "b": stricter.run_id},
            ).all())  # fmt: skip
            blue = connection.execute(
                text(f"SELECT count(*) FROM {STAGING_SCHEMA}.s2_scene WHERE item_id = ANY(:ids) AND blue_href IS NULL"),
                {"ids": [s.item_id for s in result.scenes]},
            ).scalar()
        assert decisions == {result.run_id: len(result.scenes), stricter.run_id: len(stricter.scenes)}
        assert len(stricter.scenes) < len(result.scenes) and blue == 0
    finally:
        # leave the shared staging database exactly as real runs left it
        runs = {"runs": [result.run_id, stricter.run_id]}
        with engine.begin() as connection:
            for table in ("s2_ndwi_observation", "s2_ndwi_run_scene"):
                connection.execute(text(f"DELETE FROM {STAGING_SCHEMA}.{table} WHERE run_id = ANY(:runs)"), runs)
            connection.execute(
                text(f"DELETE FROM {STAGING_SCHEMA}.s2_scene s WHERE item_id LIKE 'SYNTHETIC\\_%' AND NOT EXISTS "
                     f"(SELECT 1 FROM {STAGING_SCHEMA}.s2_ndwi_run_scene d WHERE d.item_id = s.item_id)")
            )  # fmt: skip
            connection.execute(text(f"DELETE FROM {STAGING_SCHEMA}.s2_ndwi_run WHERE run_id = ANY(:runs)"), runs)


def check_round_trip(engine, result):
    with engine.connect() as connection:
        n = connection.execute(
            text(f"SELECT count(*) FROM {STAGING_SCHEMA}.s2_ndwi_observation WHERE run_id = :r"), {"r": result.run_id}
        ).scalar()
        srid = connection.execute(
            text(f"SELECT DISTINCT ST_SRID(geometry) FROM {STAGING_SCHEMA}.s2_ndwi_parcel")
        ).scalar()
        valid = connection.execute(
            text(f"SELECT bool_and(ST_IsValid(geometry)) FROM {STAGING_SCHEMA}.s2_ndwi_parcel")
        ).scalar()
    assert n == len(result.observations)
    assert srid == 25832 and valid
