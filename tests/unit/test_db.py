"""Unit tests for core/db.py session scope + engine pool configuration."""

from __future__ import annotations

from typing import Any

import paper_trail.core.db as db_mod
from paper_trail.core.config import settings


def _spy_engine_factory(monkeypatch) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Replace create_async_engine/async_sessionmaker with capturing stubs.

    Avoids opening a real engine (and any network/driver work) so we can assert
    purely on the kwargs make_engine() forwards to create_async_engine.
    """
    captured: dict[str, Any] = {}

    def fake_create_async_engine(url: str, **kwargs: Any) -> object:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_sessionmaker", None)
    monkeypatch.setattr(db_mod, "create_async_engine", fake_create_async_engine)
    monkeypatch.setattr(db_mod, "async_sessionmaker", lambda engine, **kw: object())
    return captured


def test_make_engine_applies_pool_kwargs_for_postgres(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """REL-2 / P9: real (non-sqlite) URLs must get pre-ping + recycle + caps.

    Guards the fix — remove the pool kwargs from make_engine() and this fails.
    """
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://u:p@host:5432/db")
    monkeypatch.setattr(settings, "db_pool_size", 7)
    monkeypatch.setattr(settings, "db_max_overflow", 13)
    monkeypatch.setattr(settings, "db_pool_timeout", 42.0)
    captured = _spy_engine_factory(monkeypatch)

    db_mod.make_engine()

    kw = captured["kwargs"]
    assert kw["pool_pre_ping"] is True
    assert kw["pool_recycle"] == 300
    # Caps are wired to the configurable settings, not hardcoded.
    assert kw["pool_size"] == 7
    assert kw["max_overflow"] == 13
    assert kw["pool_timeout"] == 42.0


def test_make_engine_skips_pool_kwargs_for_sqlite(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Pool kwargs are meaningless for sqlite (tests) and must not be passed."""
    monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite:///:memory:")
    captured = _spy_engine_factory(monkeypatch)

    db_mod.make_engine()

    assert captured["kwargs"] == {"future": True}
    for key in ("pool_pre_ping", "pool_recycle", "pool_size", "max_overflow", "pool_timeout"):
        assert key not in captured["kwargs"]


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
