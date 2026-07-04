"""Integration test for live SSE streaming / commit-per-write (CRITICAL, REL-1).

Drives the *composed* path — DebateService + real DebateRepo + real
`session_scope` against a file-backed SQLite — and proves that a round written
mid-run is visible to a **separate** session before the run finishes. Under the
old single-transaction design (one session opened around the whole run, one
commit at the end) this assertion fails: the reader sees zero rounds until the
debate completes.
"""

from __future__ import annotations

import asyncio

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

import paper_trail.core.db as db_mod
from paper_trail.agents import graph as graph_mod
from paper_trail.core.config import settings
from paper_trail.core.db import session_scope
from paper_trail.models.debate import Base
from paper_trail.services.debates import DebateService


@pytest_asyncio.fixture
async def sqlite_file_db(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    url = f"sqlite+aiosqlite:///{(tmp_path / 'debates.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_sessionmaker", None)
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    yield url
    # Dispose the memoized engine so it doesn't leak into other tests.
    if db_mod._engine is not None:
        await db_mod._engine.dispose()
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_sessionmaker", None)


async def test_rounds_visible_across_sessions_before_run_finishes(
    sqlite_file_db, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    writer = DebateService(session_factory=session_scope)
    reader = DebateService(session_factory=session_scope)
    debate_id = await writer.create("the sky is blue", 3)

    release = asyncio.Event()

    class GatedGraph:
        async def astream(self, state, stream_mode="updates"):  # type: ignore[no-untyped-def]
            yield {
                "proponent": {
                    "rounds": [{"side": "proponent", "round": 1, "argument": "a", "evidence": []}]
                }
            }
            # Pause mid-run: the round above is already committed by now.
            await release.wait()
            yield {
                "judge": {
                    "verdict": "TRUE",
                    "confidence": 0.9,
                    "need_more": False,
                    "round": 1,
                }
            }
            yield {"render": {"transcript_md": "# T"}}

    monkeypatch.setattr(graph_mod, "build_graph", lambda: GatedGraph())

    run_task = asyncio.create_task(writer.run(debate_id))
    try:
        seen = None
        for _ in range(500):
            d = await reader.get(debate_id)
            if d is not None and d.rounds:
                seen = d
                break
            await asyncio.sleep(0.01)
        assert seen is not None, "round never became visible to a separate session mid-run"
        assert seen.status == "running"
        assert len(seen.rounds) == 1
    finally:
        release.set()
        result = await run_task

    assert result is not None
    assert result.status == "done"
    assert result.verdict == "TRUE"
