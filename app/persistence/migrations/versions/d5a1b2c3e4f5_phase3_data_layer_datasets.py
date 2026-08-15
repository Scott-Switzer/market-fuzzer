"""phase3_data_layer_datasets

Phase 3 (canonical market-data layer) persistence:

* Add ``runs.dataset_digest`` so each run references the exact frozen dataset
  it executed against (D5).
* Create the ``datasets`` table recording every acquired canonical panel once
  per (project_id, canonical_digest), with provider/version, request,
  provenance, quality, and artifact-manifest linkage.

Revision ID: d5a1b2c3e4f5
Revises: c78d9eca0b19
Create Date: 2026-08-14 21:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5a1b2c3e4f5"
down_revision: str | None = "c78d9eca0b19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # runs.dataset_digest linkage (D5)
    op.add_column(
        "runs",
        sa.Column("dataset_digest", sa.String(length=64), nullable=False, server_default=""),
    )

    # datasets registry table (D5)
    op.create_table(
        "datasets",
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("canonical_digest", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_version", sa.String(length=64), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.Column("quality_json", sa.JSON(), nullable=False),
        sa.Column("artifact_manifest_ref", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("dataset_id", name=op.f("pk_datasets")),
        sa.UniqueConstraint("project_id", "canonical_digest", name="uq_datasets_project_digest"),
    )
    op.create_index("ix_datasets_canonical_digest", "datasets", ["canonical_digest"], unique=False)
    op.create_index("ix_datasets_project_id", "datasets", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_datasets_project_id", table_name="datasets")
    op.drop_index("ix_datasets_canonical_digest", table_name="datasets")
    op.drop_table("datasets")
    op.drop_column("runs", "dataset_digest")
