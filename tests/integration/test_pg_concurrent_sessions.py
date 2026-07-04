"""Testcontainers-backed integration test against real Postgres.

Exercises the exact gap that let the SSE-commit bug (#1) ship behind a 99%
coverage badge: two concurrent sessions against a real Postgres, asserting that
a committed write from one is visible to the other. Marked ``integration`` so it
is excluded from the default fast CI run (`-m "not integration"`); run it with
`pytest -m integration` on a host with Docker available.

Skips cleanly when Docker / testcontainers is not available.
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("testcontainers.postgres")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from paper_trail.models.debate import Base, Debate, DebateStatus
from paper_trail.repositories.debates import DebateRepo

pytestmark = pytest.mark.integration


def _async_url(container: PostgresContainer) -> str:
    # testcontainers hands back a psycopg2 URL; swap the driver for asyncpg.
    return container.get_connection_url().replace("postgresql+psycopg2", "postgresql+asyncpg")


async def test_committed_write_visible_to_second_session() -> None:
    try:
        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception as exc:  # pragma: no cover - no docker on this host
        pytest.skip(f"Docker/Postgres unavailable: {exc}")

    try:
        engine = create_async_engine(_async_url(container))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)

        # Session A: create a debate and write a round, committing each step.
        debate_id = uuid.uuid4()
        async with maker() as sa:
            repo_a = DebateRepo(sa)
            d = Debate(
                id=debate_id,
                claim="c",
                max_rounds=3,
                status=DebateStatus.running,
                rounds=[],
            )
            sa.add(d)
            await sa.commit()
            await repo_a.update_rounds(
                debate_id,
                [{"side": "proponent", "round": 1, "argument": "a", "evidence": []}],
            )
            await sa.commit()

        # Session B (separate connection) must see the committed round.
        async with maker() as sb:
            repo_b = DebateRepo(sb)
            got = await repo_b.get(debate_id)
            assert got is not None
            assert len(got.rounds) == 1

        await engine.dispose()
    finally:
        container.stop()
