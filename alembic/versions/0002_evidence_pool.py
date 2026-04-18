"""evidence pool + structured transcript columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-18

Adds three nullable columns to `debates` to support the evidence-pool
feature (Spec 08):

- `evidence_pool` JSON: caller-supplied pre-collected evidence (NULL when
  the debate was created without one — backward compatible).
- `transcript_hash` TEXT: hex SHA-256 over the canonical transcript JSON,
  populated when the render node completes.
- `rounds_struct` JSON: structured per-round transcript with typed
  citations, populated alongside `transcript_md`.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "debates",
        sa.Column("evidence_pool", sa.JSON(), nullable=True),
    )
    op.add_column(
        "debates",
        sa.Column("transcript_hash", sa.Text(), nullable=True),
    )
    op.add_column(
        "debates",
        sa.Column("rounds_struct", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("debates", "rounds_struct")
    op.drop_column("debates", "transcript_hash")
    op.drop_column("debates", "evidence_pool")
