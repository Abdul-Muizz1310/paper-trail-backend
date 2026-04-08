"""Unit tests for repositories/debates.py.

Note: uses sqlite+aiosqlite for portability. pgvector integration is deferred to v0.2.
"""

from __future__ import annotations

from uuid import uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from paper_trail.models.debate import Base
from paper_trail.repositories.debates import DebateRepo


@pytest_asyncio.fixture
async def session():  # type: ignore[no-untyped-def]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_create_and_get(session) -> None:  # type: ignore[no-untyped-def]
    repo = DebateRepo(session)
    d = await repo.create("the sky is blue", 5)
    assert d.id is not None
    assert d.claim == "the sky is blue"
    assert d.status == "pending"
    got = await repo.get(d.id)
    assert got is not None
    assert got.id == d.id


async def test_get_unknown_returns_none(session) -> None:  # type: ignore[no-untyped-def]
    repo = DebateRepo(session)
    assert await repo.get(uuid4()) is None


async def test_update_result_sets_done(session) -> None:  # type: ignore[no-untyped-def]
    repo = DebateRepo(session)
    d = await repo.create("c", 3)
    await repo.update_result(
        d.id,
        verdict="TRUE",
        confidence=0.9,
        rounds=[{"side": "proponent", "round": 1, "argument": "a", "evidence": []}],
        transcript_md="# T",
    )
    got = await repo.get(d.id)
    assert got.verdict == "TRUE"
    assert got.confidence == 0.9
    assert got.status == "done"
    assert got.transcript_md == "# T"
    assert len(got.rounds) == 1


async def test_set_status(session) -> None:  # type: ignore[no-untyped-def]
    repo = DebateRepo(session)
    d = await repo.create("c", 3)
    await repo.set_status(d.id, "running")
    got = await repo.get(d.id)
    assert got.status == "running"


async def test_list_and_cursor_pagination(session) -> None:  # type: ignore[no-untyped-def]
    repo = DebateRepo(session)
    created = []
    for i in range(5):
        created.append(await repo.create(f"claim {i}", 3))
    items, next_cur = await repo.list_page(cursor=None, limit=2)
    assert len(items) == 2
    assert next_cur is not None
    # newest-first: the last inserted should be first
    assert items[0].claim == "claim 4"
    items2, _next_cur2 = await repo.list_page(cursor=next_cur, limit=2)
    assert len(items2) == 2
    assert items2[0].claim == "claim 2"
    # strictly older
    assert items2[0].created_at <= items[-1].created_at
