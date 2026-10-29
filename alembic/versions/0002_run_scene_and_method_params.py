"""per-run scene decisions, method thresholds in the observation key, blue_href

- s2_scene keeps item metadata only; selected/rejection_reason move to
  s2_ndwi_run_scene (one row per run and item), so a later run no longer
  overwrites what an earlier run decided.
- s2_ndwi_observation gets method_params_sha256 (digest of the thresholds)
  in its key, so a run with other thresholds adds rows instead of replacing them.
- s2_scene gets blue_href (B02 is read for the fog/haze test).

Revision ID: 0002_run_scene_method_params
Revises: 0001_iris_staging_ndwi
Create Date: 2026-10-29
"""

import hashlib
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_run_scene_method_params"
down_revision: Union[str, Sequence[str], None] = "0001_iris_staging_ndwi"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "iris_staging"

# Frozen copy of iris_ndwi.pipeline.METHOD_PARAMS at this revision (migrations must not change later).
METHOD_PARAMS = (
    "target_crs",
    "valid_scl_classes",
    "max_blue_reflectance",
    "require_positive_reflectance",
    "max_below_offset_fraction",
    "min_valid_fraction",
    "water_threshold",
)


def _digest(config: dict) -> str:
    payload = json.dumps({key: config.get(key) for key in METHOD_PARAMS}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def upgrade() -> None:
    op.add_column("s2_scene", sa.Column("blue_href", sa.Text), schema=SCHEMA)

    op.create_table(
        "s2_ndwi_run_scene",
        sa.Column("run_id", sa.String(64), sa.ForeignKey(f"{SCHEMA}.s2_ndwi_run.run_id"), primary_key=True),
        sa.Column("item_id", sa.String(64), sa.ForeignKey(f"{SCHEMA}.s2_scene.item_id"), primary_key=True),
        sa.Column("selected", sa.Boolean, nullable=False),
        sa.Column("rejection_reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("idx_s2_ndwi_run_scene_item", "s2_ndwi_run_scene", ["item_id"], schema=SCHEMA)
    # The latest decision per item is all that 0001 kept; carry it over.
    op.execute(
        f"INSERT INTO {SCHEMA}.s2_ndwi_run_scene (run_id, item_id, selected, rejection_reason) "
        f"SELECT run_id, item_id, selected, rejection_reason FROM {SCHEMA}.s2_scene"
    )
    op.drop_column("s2_scene", "selected", schema=SCHEMA)
    op.drop_column("s2_scene", "rejection_reason", schema=SCHEMA)
    op.drop_column("s2_scene", "run_id", schema=SCHEMA)

    op.add_column("s2_ndwi_observation", sa.Column("method_params_sha256", sa.String(64)), schema=SCHEMA)
    connection = op.get_bind()
    runs = connection.execute(sa.text(f"SELECT run_id, manifest FROM {SCHEMA}.s2_ndwi_run")).all()
    for run_id, manifest in runs:
        digest = manifest.get("method_params_sha256") or _digest(manifest["config"])
        connection.execute(
            sa.text(
                f"UPDATE {SCHEMA}.s2_ndwi_observation SET method_params_sha256 = :d, "
                "observation_id = observation_id || ':' || :short WHERE run_id = :r"
            ),
            {"d": digest, "short": digest[:8], "r": run_id},
        )
    op.alter_column("s2_ndwi_observation", "method_params_sha256", nullable=False, schema=SCHEMA)
    op.drop_constraint("uq_s2_ndwi_observation_key", "s2_ndwi_observation", schema=SCHEMA)
    op.create_unique_constraint(
        "uq_s2_ndwi_observation_key",
        "s2_ndwi_observation",
        ["parcel_id", "item_id", "method_version", "method_params_sha256"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Observations that differ only in their thresholds cannot share the 0001 key; keep the newest.
    op.execute(
        f"DELETE FROM {SCHEMA}.s2_ndwi_observation o USING {SCHEMA}.s2_ndwi_observation n "
        "WHERE o.parcel_id = n.parcel_id AND o.item_id = n.item_id AND o.method_version = n.method_version "
        "AND o.updated_at < n.updated_at"
    )
    op.drop_constraint("uq_s2_ndwi_observation_key", "s2_ndwi_observation", schema=SCHEMA)
    op.execute(
        f"UPDATE {SCHEMA}.s2_ndwi_observation SET observation_id = parcel_id || ':' || item_id || ':' || method_version"
    )
    op.create_unique_constraint(
        "uq_s2_ndwi_observation_key", "s2_ndwi_observation", ["parcel_id", "item_id", "method_version"], schema=SCHEMA
    )
    op.drop_column("s2_ndwi_observation", "method_params_sha256", schema=SCHEMA)

    op.add_column("s2_scene", sa.Column("selected", sa.Boolean), schema=SCHEMA)
    op.add_column("s2_scene", sa.Column("rejection_reason", sa.Text), schema=SCHEMA)
    op.add_column(
        "s2_scene", sa.Column("run_id", sa.String(64), sa.ForeignKey(f"{SCHEMA}.s2_ndwi_run.run_id")), schema=SCHEMA
    )
    op.execute(  # the latest decision per item, as 0001 kept it
        f"UPDATE {SCHEMA}.s2_scene s SET selected = d.selected, rejection_reason = d.rejection_reason, run_id = d.run_id "
        f"FROM (SELECT DISTINCT ON (item_id) * FROM {SCHEMA}.s2_ndwi_run_scene ORDER BY item_id, updated_at DESC) d "
        "WHERE d.item_id = s.item_id"
    )
    op.execute(f"DELETE FROM {SCHEMA}.s2_scene WHERE run_id IS NULL")
    op.alter_column("s2_scene", "selected", nullable=False, schema=SCHEMA)
    op.alter_column("s2_scene", "run_id", nullable=False, schema=SCHEMA)
    op.drop_table("s2_ndwi_run_scene", schema=SCHEMA)
    op.drop_column("s2_scene", "blue_href", schema=SCHEMA)
