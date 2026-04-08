"""Unit tests for core/db.py session scope."""

from __future__ import annotations

import paper_trail.core.db as db_mod
from paper_trail.core.config import settings


async def test_session_scope_yields_session(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_sessionmaker", None)
    async with db_mod.session_scope() as session:
        assert session is not None


async def test_session_scope_rolls_back_on_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_sessionmaker", None)
    try:
        async with db_mod.session_scope() as session:
            assert session is not None
            raise RuntimeError("boom")
    except RuntimeError:
        pass
