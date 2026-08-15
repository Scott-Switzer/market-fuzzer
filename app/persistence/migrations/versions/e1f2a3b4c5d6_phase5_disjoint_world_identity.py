"""phase5_disjoint_world_identity

Revision ID: e1f2a3b4c5d6
Revises: d5a1b2c3e4f5
Create Date: 2026-08-15 12:00:00.000000

Phase 5 disjoint-evidence (item 1 of the next sequence): add the canonical
EFFECTIVE-WORLD identity to ``scenario_worlds`` and enforce, at the database
layer, that an effective world identity can appear at most once per campaign.

This is the DB-level backstop for the invariant:
  primary.world_hash not in confirmation_world_hashes
  and all confirmation_world_hashes pairwise distinct.
A confirmation world can therefore NEVER reuse the primary search world's
identity, nor duplicate another confirmation world's identity -- even if the
application-level check were bypassed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d5a1b2c3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add the effective-world identity column (nullable first so we can backfill).
    with op.batch_alter_table("scenario_worlds") as batch:
        batch.add_column(sa.Column("world_hash", sa.String(length=64), nullable=True))

    # Backfill any pre-existing rows with a unique identity derived from the row
    # primary key (a uuid, already unique per row). New rows receive the real
    # content-derived hash from the application layer.
    op.execute("UPDATE scenario_worlds SET world_hash = id WHERE world_hash IS NULL OR world_hash = ''")

    # Make it required and enforce per-campaign uniqueness of the effective identity.
    with op.batch_alter_table("scenario_worlds") as batch:
        batch.alter_column("world_hash", nullable=False, existing_type=sa.String(length=64))
        batch.create_unique_constraint(
            "uq_scenario_worlds_campaign_world_hash",
            ["campaign_id", "world_hash"],
        )


def downgrade() -> None:
    with op.batch_alter_table("scenario_worlds") as batch:
        batch.drop_constraint("uq_scenario_worlds_campaign_world_hash", type_="unique")
        batch.drop_column("world_hash")
