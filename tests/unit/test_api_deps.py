"""Unit test for api/deps.py — exercises the real session_scope wiring."""

from __future__ import annotations

import paper_trail.core.db as db_mod
from paper_trail.api.deps import get_service
from paper_trail.core.config import settings
from paper_trail.services.debates import DebateService


async def test_get_service_yields_debate_service(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_sessionmaker", None)
    gen = get_service()
    svc = await gen.__anext__()
    assert isinstance(svc, DebateService)
    try:
        await gen.__anext__()
    except StopAsyncIteration:
        pass
