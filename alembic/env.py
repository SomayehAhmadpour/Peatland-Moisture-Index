"""Alembic environment for the iris_staging schema only.

The version table lives in iris_staging too, so migrations never touch
iris_core or public.
"""

from alembic import context
from sqlalchemy import text

from iris_ndwi.staging import STAGING_SCHEMA, engine_from_env, metadata


def run_migrations_offline() -> None:
    context.configure(
        url="postgresql+psycopg://",
        target_metadata=metadata,
        literal_binds=True,
        version_table_schema=STAGING_SCHEMA,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = engine_from_env()
    with engine.connect() as connection:
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {STAGING_SCHEMA}"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=metadata,
            version_table_schema=STAGING_SCHEMA,
            include_schemas=True,
            include_name=lambda name, type_, parent: type_ != "schema" or name == STAGING_SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
