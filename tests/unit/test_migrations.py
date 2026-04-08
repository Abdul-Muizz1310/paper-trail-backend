"""Tests for alembic initial migration (run against sqlite)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import create_engine, inspect

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0001_initial_debate_table.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "paper_trail_migration_0001", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_initial_migration_upgrade_and_downgrade() -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mod = _load_migration()
    engine = create_engine("sqlite:///:memory:")

    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        ops = Operations(ctx)
        # Patch the global `op` inside the migration module to use our Operations
        mod.op = ops  # type: ignore[attr-defined]

        mod.upgrade()
        insp = inspect(conn)
        assert "debates" in insp.get_table_names()
        cols = {c["name"] for c in insp.get_columns("debates")}
        expected = {
            "id",
            "claim",
            "max_rounds",
            "status",
            "verdict",
            "confidence",
            "rounds",
            "transcript_md",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(cols)

        mod.downgrade()
        insp = inspect(conn)
        assert "debates" not in insp.get_table_names()
