"""iris_staging writer. Unit checks run always; the PostGIS round trip needs
IRIS_TEST_DATABASE_URL (provided by `docker compose run --rm tests`)."""

import os

import pytest
from sqlalchemy import create_engine, text

from iris_ndwi.staging import STAGING_SCHEMA, StagingGuardError, engine_from_env, metadata, write_run


def test_all_tables_live_in_staging_schema():
    assert metadata.tables
    assert {t.schema for t in metadata.tables.values()} == {STAGING_SCHEMA}
    assert not any("iris_core" in name for name in metadata.tables)


def test_missing_database_url_is_an_error(monkeypatch):
    monkeypatch.delenv("IRIS_STAGING_DATABASE_URL", raising=False)
    with pytest.raises(StagingGuardError, match="IRIS_STAGING_DATABASE_URL"):
        engine_from_env()


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
    try:
        first = write_run(result, engine)
        second = write_run(result, engine)  # same ids -> update, not duplicate
        assert first == second
        check_round_trip(engine, result)
    finally:
        # leave the shared staging database exactly as real runs left it
        with engine.begin() as connection:
            for table in ("s2_ndwi_observation", "s2_scene", "s2_ndwi_run"):
                connection.execute(text(f"DELETE FROM {STAGING_SCHEMA}.{table} WHERE run_id = :r"), {"r": result.run_id})


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
