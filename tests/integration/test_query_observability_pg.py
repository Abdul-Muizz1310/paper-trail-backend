"""Spec 11 O5-O6: a debate's queries, background task included, are listed by request id.

The composed flow against a real Postgres: the app's own engine and the
library's Postgres store on the same database, the real ``DebateService``
writing through the real repository, only the LangGraph graph stubbed (no LLM).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

pytest.importorskip("testcontainers.postgres")

from httpx import ASGITransport, AsyncClient
from testcontainers.postgres import PostgresContainer

from paper_trail.agents import graph as graph_mod
from paper_trail.core import db
from paper_trail.core.config import settings
from paper_trail.main import create_app
from paper_trail.models.debate import Base
from paper_trail.platform import platform_token
from tests.unit.test_platform_contract_v2 import BASTION_URL_ENV, Keys

pytestmark = pytest.mark.integration


class _StubGraph:
    """The node updates a one-round debate streams, without an LLM or a search."""

    async def astream(self, state: Any, stream_mode: str) -> AsyncIterator[dict[str, Any]]:
        round_ = {"side": "proponent", "round": 1, "argument": "a", "evidence": []}
        yield {"proponent": {"rounds": [round_]}}
        yield {"judge": {"verdict": "TRUE", "confidence": 0.9, "round": 1}}
        yield {
            "render": {
                "transcript_md": "# Debate",
                "rounds_struct": [
                    {"side": "proponent", "round": 1, "argument_md": "a", "citations": []}
                ],
                "transcript_hash": "f" * 64,
            }
        }


@pytest.fixture
async def pg_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, Keys]]:
    try:
        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception as exc:  # pragma: no cover - no docker on this host
        pytest.skip(f"Docker/Postgres unavailable: {exc}")
    try:
        url = container.get_connection_url().replace("postgresql+psycopg2", "postgresql+asyncpg")
        monkeypatch.setattr(settings, "database_url", url)
        monkeypatch.setattr(settings, "demo_mode", False)
        monkeypatch.setattr(settings, "app_env", "test")
        monkeypatch.setattr(settings, "rate_limit_enabled", False)
        monkeypatch.setattr(db, "_engine", None)
        monkeypatch.setattr(db, "_sessionmaker", None)
        monkeypatch.delenv(BASTION_URL_ENV, raising=False)
        monkeypatch.setattr(graph_mod, "build_graph", lambda: _StubGraph())
        keys = Keys(monkeypatch)
        platform_token.reset_public_key_cache()

        engine = db.make_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        app = create_app()
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                yield client, keys
        await engine.dispose()
    finally:
        platform_token.reset_public_key_cache()
        container.stop()


async def _start_debate(client: AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post(
        "/debates",
        json={"claim": "Water boils at 100 C at sea level.", "max_rounds": 1},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    debate_id = str(response.json()["debate_id"])
    # The background run finished inside the request's ASGI call.
    done = await client.get(f"/debates/{debate_id}", headers=headers)
    assert done.json()["status"] == "done", done.text
    return debate_id


async def test_o5_the_background_tasks_queries_belong_to_the_post_that_started_it(
    pg_client: tuple[AsyncClient, Keys],
) -> None:
    client, keys = pg_client
    bastion = {"X-Platform-Token": keys.bastion_token()}
    await _start_debate(client, {**bastion, "X-Request-Id": "R-debate-1"})
    await _start_debate(client, {"X-Platform-Token": keys.bastion_token()})  # untagged

    listed = await client.get(
        "/_slowquery/queries",
        params={"request_id": "R-debate-1"},
        headers={"X-Platform-Token": keys.bastion_token()},
    )
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    fingerprints = [row["fingerprint"] for row in rows]
    # The endpoint's insert ...
    assert any(fp.startswith("insert into debates") for fp in fingerprints), fingerprints
    # ... and the background run's writes, which only DebateService.run issues.
    updates = [row for row in rows if row["fingerprint"].startswith("update debates")]
    assert updates, fingerprints
    # One debate's worth: the untagged second debate added nothing to R.
    inserts = [row for row in rows if row["fingerprint"].startswith("insert into debates")]
    assert sum(row["call_count"] for row in inserts) == 1
    assert all(row["p50_ms"] is None for row in rows)
    assert not any("slowquery." in fp for fp in fingerprints)


async def test_o6_the_measure_route_is_bastion_only(pg_client: tuple[AsyncClient, Keys]) -> None:
    client, keys = pg_client
    url = "/_slowquery/queries"
    params = {"request_id": "R-debate-1"}
    missing = await client.get(url, params=params)
    frontend = await client.get(
        url, params=params, headers={"X-Platform-Token": keys.frontend_token()}
    )
    assert missing.status_code == 401
    assert frontend.status_code == 403
