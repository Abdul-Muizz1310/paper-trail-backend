"""initial debate table

Revision ID: 0001
Revises:
Create Date: 2026-04-09

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "debates",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("max_rounds", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("verdict", sa.String(length=20), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("rounds", sa.JSON(), nullable=False),
        sa.Column("transcript_md", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_debates_status", "debates", ["status"])
    op.create_index("ix_debates_created_at", "debates", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_debates_created_at", table_name="debates")
    op.drop_index("ix_debates_status", table_name="debates")
    op.drop_table("debates")
