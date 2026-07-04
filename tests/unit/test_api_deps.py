"""Unit test for api/deps.py — the DebateService provider wiring."""

from __future__ import annotations

import paper_trail.core.db as db_mod
from paper_trail.api.deps import get_service
from paper_trail.core.config import settings
from paper_trail.core.db import session_scope
from paper_trail.services.debates import DebateService


def test_get_service_returns_debate_service(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_sessionmaker", None)
    svc = get_service()
    assert isinstance(svc, DebateService)
    # Bound to the real session_scope so every op uses a short-lived session
    # (no request-scoped connection pinned for the whole request/stream).
    assert svc._session_factory is session_scope
