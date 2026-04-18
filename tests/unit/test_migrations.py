"""Tests for alembic migrations (run against sqlite)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import create_engine, inspect

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0001_initial_debate_table.py"
)
MIGRATION_0002_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0002_evidence_pool.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("paper_trail_migration_0001", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_migration_0002() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "paper_trail_migration_0002", MIGRATION_0002_PATH
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


def test_evidence_pool_migration_upgrade_and_downgrade() -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mod0001 = _load_migration()
    mod0002 = _load_migration_0002()
    engine = create_engine("sqlite:///:memory:")

    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        ops = Operations(ctx)
        mod0001.op = ops  # type: ignore[attr-defined]
        mod0002.op = ops  # type: ignore[attr-defined]

        mod0001.upgrade()
        mod0002.upgrade()
        insp = inspect(conn)
        cols = {c["name"] for c in insp.get_columns("debates")}
        assert "evidence_pool" in cols
        assert "transcript_hash" in cols
        assert "rounds_struct" in cols

        mod0002.downgrade()
        insp = inspect(conn)
        cols_after = {c["name"] for c in insp.get_columns("debates")}
        assert "evidence_pool" not in cols_after
        assert "transcript_hash" not in cols_after
        assert "rounds_struct" not in cols_after
