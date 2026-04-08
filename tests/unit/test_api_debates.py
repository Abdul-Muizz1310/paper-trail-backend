"""Unit tests for api/routers/debates.py."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from paper_trail.api import deps
from paper_trail.api.routers import debates as debates_router_mod
from paper_trail.main import app


class FakeDebate:
    def __init__(self, id: UUID, claim: str, max_rounds: int) -> None:
        self.id = id
        self.claim = claim
        self.max_rounds = max_rounds
        self.status = "pending"
        self.verdict: str | None = None
        self.confidence: float | None = None
        self.rounds: list[dict[str, Any]] = []
        self.transcript_md: str | None = None
        from datetime import datetime

        self.created_at = datetime.utcnow()


class FakeDebateService:
    def __init__(self) -> None:
        self.store: dict[UUID, FakeDebate] = {}
        self.run_called_with: list[UUID] = []
        self.run_event = asyncio.Event()
        self._status_sequence: list[str] = ["running", "running", "done"]

    async def create(self, claim: str, max_rounds: int) -> UUID:
        d = FakeDebate(uuid4(), claim, max_rounds)
        d.verdict = "TRUE"
        d.confidence = 0.9
        d.rounds = [{"side": "proponent", "round": 1, "argument": "a", "evidence": []}]
        d.transcript_md = "# transcript"
        self.store[d.id] = d
        return d.id

    async def run(self, debate_id: UUID) -> Any:
        self.run_called_with.append(debate_id)
        d = self.store.get(debate_id)
        if d is not None:
            d.status = "done"
        self.run_event.set()
        return d

    async def get(self, debate_id: UUID) -> Any:
        d = self.store.get(debate_id)
        if d is not None and self._status_sequence:
            d.status = self._status_sequence.pop(0)
        return d

    async def list(
        self, cursor: str | None, limit: int = 50
    ) -> tuple[list[Any], str | None]:
        items = list(self.store.values())[:limit]
        return items, "next-cursor-abc"


@pytest.fixture
def fake_service() -> FakeDebateService:
    return FakeDebateService()


@pytest.fixture
def client_with_fake(fake_service: FakeDebateService):
    async def _override():
        yield fake_service

    app.dependency_overrides[deps.get_service] = _override
    yield fake_service
    app.dependency_overrides.clear()


async def _make_client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_create_debate_returns_201_and_shape(client_with_fake) -> None:
    async with await _make_client() as c:
        r = await c.post("/debates", json={"claim": "the sky is blue", "max_rounds": 3})
    assert r.status_code == 201
    body = r.json()
    assert "debate_id" in body
    assert body["stream_url"].endswith("/stream")
    assert f"/debates/{body['debate_id']}/stream" == body["stream_url"]


async def test_create_debate_empty_claim_422(client_with_fake) -> None:
    async with await _make_client() as c:
        r = await c.post("/debates", json={"claim": "", "max_rounds": 3})
    assert r.status_code == 422


async def test_create_debate_max_rounds_too_high_422(client_with_fake) -> None:
    async with await _make_client() as c:
        r = await c.post("/debates", json={"claim": "x", "max_rounds": 11})
    assert r.status_code == 422


async def test_create_debate_triggers_background_run(client_with_fake) -> None:
    async with await _make_client() as c:
        r = await c.post("/debates", json={"claim": "x", "max_rounds": 3})
    assert r.status_code == 201
    # background tasks finish before client context exits in httpx ASGI transport
    await asyncio.wait_for(client_with_fake.run_event.wait(), timeout=2.0)
    assert len(client_with_fake.run_called_with) == 1


async def test_get_debate_happy(client_with_fake) -> None:
    did = await client_with_fake.create("hi", 3)
    async with await _make_client() as c:
        r = await c.get(f"/debates/{did}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(did)
    assert body["claim"] == "hi"


async def test_get_debate_unknown_404(client_with_fake) -> None:
    async with await _make_client() as c:
        r = await c.get(f"/debates/{uuid4()}")
    assert r.status_code == 404


async def test_list_debates(client_with_fake) -> None:
    await client_with_fake.create("a", 3)
    await client_with_fake.create("b", 3)
    async with await _make_client() as c:
        r = await c.get("/debates", params={"limit": 2})
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "next_cursor" in body
    assert len(body["items"]) == 2


async def test_transcript_markdown_happy(client_with_fake) -> None:
    did = await client_with_fake.create("hi", 3)
    async with await _make_client() as c:
        r = await c.get(f"/debates/{did}/transcript.md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert r.text == "# transcript"


async def test_transcript_markdown_missing_404(client_with_fake) -> None:
    did = await client_with_fake.create("hi", 3)
    client_with_fake.store[did].transcript_md = None
    async with await _make_client() as c:
        r = await c.get(f"/debates/{did}/transcript.md")
    assert r.status_code == 404


async def test_stream_emits_state_and_done(
    client_with_fake, monkeypatch
) -> None:
    monkeypatch.setattr(debates_router_mod, "STREAM_POLL_SECONDS", 0.01)
    monkeypatch.setattr(debates_router_mod, "STREAM_MAX_SECONDS", 5.0)
    did = await client_with_fake.create("hi", 3)
    # Reset the get() status sequence so we see running -> done evolution
    client_with_fake._status_sequence = ["running", "running", "done"]
    client_with_fake.store[did].status = "pending"

    seen_state = False
    seen_done = False
    async with await _make_client() as c:
        async with c.stream("GET", f"/debates/{did}/stream") as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            async for line in r.aiter_lines():
                if line.startswith("event: state"):
                    seen_state = True
                if line.startswith("event: done"):
                    seen_done = True
                    break
    assert seen_state
    assert seen_done
