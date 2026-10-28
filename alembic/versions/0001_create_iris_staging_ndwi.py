"""create iris_staging NDWI tables

Revision ID: 0001_iris_staging_ndwi
Revises:
Create Date: 2026-10-27
"""

from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_iris_staging_ndwi"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "iris_staging"


def _audit() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "s2_ndwi_run",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("synthetic_data", sa.Boolean, nullable=False),
        sa.Column("method_version", sa.String(64), nullable=False),
        sa.Column("config_sha256", sa.String(64), nullable=False),
        sa.Column("manifest", postgresql.JSONB, nullable=False),
        *_audit(),
        schema=SCHEMA,
    )

    op.create_table(
        "s2_ndwi_parcel",
        sa.Column("parcel_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_ref", sa.String(255), nullable=False),
        sa.Column("licence", sa.String(255), nullable=False),
        sa.Column("area_m2", sa.Float, nullable=False),
        sa.Column("geometry_repaired", sa.Boolean, nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.types.Geometry("MULTIPOLYGON", srid=25832, spatial_index=False),
            nullable=False,
        ),
        *_audit(),
        sa.CheckConstraint("ST_IsValid(geometry)", name="ck_s2_ndwi_parcel_valid_geometry"),
        schema=SCHEMA,
    )
    op.create_index("idx_s2_ndwi_parcel_geometry", "s2_ndwi_parcel", ["geometry"], schema=SCHEMA, postgresql_using="gist")

    op.create_table(
        "s2_scene",
        sa.Column("item_id", sa.String(64), primary_key=True),
        sa.Column("collection", sa.String(64)),
        sa.Column("acquired_at", sa.DateTime(timezone=True)),
        sa.Column("platform", sa.String(32)),
        sa.Column("mgrs_tile", sa.String(8)),
        sa.Column("processing_baseline", sa.String(8)),
        sa.Column("generation_time", sa.String(40)),
        sa.Column("scene_cloud_pct", sa.Float),
        sa.Column("boa_offset_applied_flag", sa.Boolean),
        sa.Column("boa_offset", sa.Float),
        sa.Column("reflectance_scale", sa.Float),
        sa.Column("sun_elevation", sa.Float),
        sa.Column("selected", sa.Boolean, nullable=False),
        sa.Column("rejection_reason", sa.Text),
        sa.Column("green_href", sa.Text),
        sa.Column("nir_href", sa.Text),
        sa.Column("scl_href", sa.Text),
        sa.Column("run_id", sa.String(64), sa.ForeignKey(f"{SCHEMA}.s2_ndwi_run.run_id"), nullable=False),
        *_audit(),
        schema=SCHEMA,
    )
    op.create_index("idx_s2_scene_acquired_at", "s2_scene", ["acquired_at"], schema=SCHEMA)

    op.create_table(
        "s2_ndwi_observation",
        sa.Column("observation_id", sa.String(200), primary_key=True),
        sa.Column("parcel_id", sa.String(64), sa.ForeignKey(f"{SCHEMA}.s2_ndwi_parcel.parcel_id"), nullable=False),
        sa.Column("item_id", sa.String(64), sa.ForeignKey(f"{SCHEMA}.s2_scene.item_id"), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("method_version", sa.String(64), nullable=False),
        sa.Column("n_pixels", sa.Integer, nullable=False),
        sa.Column("n_nodata", sa.Integer, nullable=False),
        sa.Column("n_scl_masked", sa.Integer, nullable=False),
        sa.Column("n_bright_surface", sa.Integer, nullable=False),
        sa.Column("n_non_positive_reflectance", sa.Integer, nullable=False),
        sa.Column("n_zero_denominator", sa.Integer, nullable=False),
        sa.Column("n_out_of_range", sa.Integer, nullable=False),
        sa.Column("n_valid", sa.Integer, nullable=False),
        sa.Column("valid_fraction", sa.Float, nullable=False),
        sa.Column("ndwi_mean", sa.Float),
        sa.Column("ndwi_median", sa.Float),
        sa.Column("ndwi_std", sa.Float),
        sa.Column("ndwi_p25", sa.Float),
        sa.Column("ndwi_p75", sa.Float),
        sa.Column("ndwi_min", sa.Float),
        sa.Column("ndwi_max", sa.Float),
        sa.Column("water_fraction", sa.Float),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("rejection_reason", sa.String(64)),
        sa.Column("run_id", sa.String(64), sa.ForeignKey(f"{SCHEMA}.s2_ndwi_run.run_id"), nullable=False),
        *_audit(),
        sa.UniqueConstraint("parcel_id", "item_id", "method_version", name="uq_s2_ndwi_observation_key"),
        sa.CheckConstraint("status IN ('accepted', 'rejected')", name="ck_s2_ndwi_observation_status"),
        sa.CheckConstraint("valid_fraction BETWEEN 0 AND 1", name="ck_s2_ndwi_observation_fraction"),
        sa.CheckConstraint(
            "ndwi_median IS NULL OR ndwi_median BETWEEN -1 AND 1", name="ck_s2_ndwi_observation_range"
        ),
        sa.CheckConstraint(
            "n_pixels = n_valid + n_nodata + n_scl_masked + n_bright_surface + n_non_positive_reflectance"
            " + n_zero_denominator + n_out_of_range",
            name="ck_s2_ndwi_observation_counts",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_s2_ndwi_observation_parcel_time", "s2_ndwi_observation", ["parcel_id", "acquired_at"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_table("s2_ndwi_observation", schema=SCHEMA)
    op.drop_table("s2_scene", schema=SCHEMA)
    op.drop_table("s2_ndwi_parcel", schema=SCHEMA)
    op.drop_table("s2_ndwi_run", schema=SCHEMA)
