"""Alembic against a real Postgres, run the way the entrypoint runs it (spec 06, M6).

The container applies ``alembic upgrade head`` on every boot, so the step must
build the schema on an empty database and be a no-op the second time. Run as a
subprocess from the repository root, exactly the command docker-entrypoint.sh
runs. 0001 creates the pgvector extension, hence the pgvector image.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("testcontainers.postgres")

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from paper_trail.models.debate import Debate

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def _upgrade_head(async_url: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": async_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


async def test_m6_upgrade_head_builds_the_schema_and_is_a_no_op_the_second_time() -> None:
    try:
        container = PostgresContainer("pgvector/pgvector:pg16")
        container.start()
    except Exception as exc:  # pragma: no cover - no docker on this host
        pytest.skip(f"Docker/Postgres unavailable: {exc}")
    try:
        async_url = container.get_connection_url().replace(
            "postgresql+psycopg2", "postgresql+asyncpg"
        )

        first = _upgrade_head(async_url)
        assert first.returncode == 0, first.stderr
        assert "Running upgrade" in first.stderr

        engine = create_async_engine(async_url)
        async with engine.connect() as conn:
            head = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
            columns = await conn.run_sync(
                lambda sync: {c["name"] for c in inspect(sync).get_columns("debates")}
            )
        assert head == "0002"
        assert columns == {c.name for c in Debate.__table__.columns}

        second = _upgrade_head(async_url)
        assert second.returncode == 0, second.stderr
        assert "Running upgrade" not in second.stderr
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT count(*) FROM alembic_version"))).scalar_one()
        assert rows == 1
        await engine.dispose()
    finally:
        container.stop()
