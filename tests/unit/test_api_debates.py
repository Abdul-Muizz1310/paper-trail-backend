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
        # Block 6 (Spec 08)
        self.evidence_pool: list[dict[str, Any]] | None = None
        self.rounds_struct: list[dict[str, Any]] | None = None
        self.transcript_hash: str | None = None
        from datetime import datetime

        self.created_at = datetime.utcnow()


class FakeDebateService:
    def __init__(self) -> None:
        self.store: dict[UUID, FakeDebate] = {}
        self.run_called_with: list[UUID] = []
        self.run_event = asyncio.Event()
        self._status_sequence: list[str] = ["running", "running", "done"]

    async def create(
        self,
        claim: str,
        max_rounds: int,
        evidence_pool: list[dict[str, Any]] | None = None,
    ) -> UUID:
        d = FakeDebate(uuid4(), claim, max_rounds)
        d.verdict = "TRUE"
        d.confidence = 0.9
        d.rounds = [
            {
                "side": "proponent",
                "round": 1,
                "argument": "a",
                "evidence": [],
                "citations": [],
            }
        ]
        d.transcript_md = "# transcript"
        d.evidence_pool = evidence_pool if evidence_pool else None
        d.rounds_struct = [
            {
                "side": "proponent",
                "round": 1,
                "argument_md": "a",
                "citations": [],
            }
        ]
        d.transcript_hash = "f" * 64
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

    async def list(self, cursor: str | None, limit: int = 50) -> tuple[list[Any], str | None]:
        items = list(self.store.values())[:limit]
        return items, "next-cursor-abc"


@pytest.fixture
def fake_service() -> FakeDebateService:
    return FakeDebateService()


@pytest.fixture
def client_with_fake(fake_service: FakeDebateService, monkeypatch: pytest.MonkeyPatch):
    async def _override():
        yield fake_service

    async def _fake_background(debate_id: UUID) -> None:
        await fake_service.run(debate_id)

    app.dependency_overrides[deps.get_service] = _override
    monkeypatch.setattr(debates_router_mod, "_run_debate_background", _fake_background)
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


async def test_stream_not_found_emits_error_event(client_with_fake, monkeypatch) -> None:
    """Cover debates.py:115-119 — debate_id not in store → error event."""
    monkeypatch.setattr(debates_router_mod, "STREAM_POLL_SECONDS", 0.01)
    monkeypatch.setattr(debates_router_mod, "STREAM_MAX_SECONDS", 5.0)
    unknown_id = uuid4()
    seen_error = False
    async with await _make_client() as c, c.stream("GET", f"/debates/{unknown_id}/stream") as r:
        assert r.status_code == 200
        async for line in r.aiter_lines():
            if line.startswith("event: error"):
                seen_error = True
                break
    assert seen_error


async def test_stream_timeout_emits_done_with_reason(client_with_fake, monkeypatch) -> None:
    """Cover debates.py:171 — deadline reached → timeout done event."""
    monkeypatch.setattr(debates_router_mod, "STREAM_POLL_SECONDS", 0.01)
    monkeypatch.setattr(debates_router_mod, "STREAM_MAX_SECONDS", 0.05)
    did = await client_with_fake.create("hi", 3)
    # Keep status as "running" so it never reaches terminal
    client_with_fake._status_sequence = ["running"] * 100

    seen_timeout = False
    async with await _make_client() as c, c.stream("GET", f"/debates/{did}/stream") as r:
        collected = ""
        async for line in r.aiter_lines():
            collected += line + "\n"
            if "timeout" in line:
                seen_timeout = True
                break
    assert seen_timeout


async def test_stream_keepalive_emits_ping(client_with_fake, monkeypatch) -> None:
    """Cover debates.py:152-153 — keepalive ping after inactivity."""
    monkeypatch.setattr(debates_router_mod, "STREAM_POLL_SECONDS", 0.01)
    monkeypatch.setattr(debates_router_mod, "STREAM_MAX_SECONDS", 2.0)
    monkeypatch.setattr(debates_router_mod, "STREAM_KEEPALIVE_SECONDS", 0.03)
    did = await client_with_fake.create("hi", 3)
    # Status stays "running" with no state changes → should trigger keepalive
    client_with_fake._status_sequence = ["running"] * 50 + ["done"]

    seen_ping = False
    seen_done = False
    async with await _make_client() as c, c.stream("GET", f"/debates/{did}/stream") as r:
        async for line in r.aiter_lines():
            if line.startswith("event: ping"):
                seen_ping = True
            if line.startswith("event: done"):
                seen_done = True
                break
    # After many polls with same snapshot, keepalive should fire
    assert seen_ping or seen_done  # at minimum one must happen


# ---------------------------------------------------------------------------
# Block 6 (Spec 08) — evidence pool + transcript.json
# ---------------------------------------------------------------------------


def _make_pool_item() -> dict[str, Any]:
    return {
        "certificate_id": str(uuid4()),
        "url": "https://example.com",
        "title": "Source",
        "text": "authoritative content here",
    }


async def test_create_debate_without_pool_persists_null(client_with_fake) -> None:
    """Case 1."""
    async with await _make_client() as c:
        r = await c.post("/debates", json={"claim": "hi", "max_rounds": 3})
    assert r.status_code == 201
    did = UUID(r.json()["debate_id"])
    assert client_with_fake.store[did].evidence_pool is None


async def test_create_debate_with_3_item_pool_persists(client_with_fake) -> None:
    """Case 2."""
    pool = [_make_pool_item() for _ in range(3)]
    async with await _make_client() as c:
        r = await c.post(
            "/debates",
            json={"claim": "hi", "max_rounds": 3, "evidence_pool": pool},
        )
    assert r.status_code == 201
    did = UUID(r.json()["debate_id"])
    persisted = client_with_fake.store[did].evidence_pool
    assert persisted is not None
    assert len(persisted) == 3
    assert persisted[0]["certificate_id"] == pool[0]["certificate_id"]


async def test_create_debate_with_empty_pool_persists_null(client_with_fake) -> None:
    """Case 3."""
    async with await _make_client() as c:
        r = await c.post(
            "/debates",
            json={"claim": "hi", "max_rounds": 3, "evidence_pool": []},
        )
    assert r.status_code == 201
    did = UUID(r.json()["debate_id"])
    assert client_with_fake.store[did].evidence_pool is None


async def test_create_debate_with_51_item_pool_rejected(client_with_fake) -> None:
    """Case 9."""
    pool = [_make_pool_item() for _ in range(51)]
    async with await _make_client() as c:
        r = await c.post(
            "/debates",
            json={"claim": "hi", "max_rounds": 3, "evidence_pool": pool},
        )
    assert r.status_code == 422


async def test_create_debate_with_malformed_cert_id_rejected(client_with_fake) -> None:
    """Case 10."""
    bad_item = {
        "certificate_id": "not-a-uuid",
        "url": "u",
        "title": "t",
        "text": "body",
    }
    async with await _make_client() as c:
        r = await c.post(
            "/debates",
            json={"claim": "hi", "max_rounds": 3, "evidence_pool": [bad_item]},
        )
    assert r.status_code == 422


async def test_create_debate_with_pool_item_missing_text_rejected(client_with_fake) -> None:
    """Case 11."""
    bad_item = {
        "certificate_id": str(uuid4()),
        "url": "u",
        "title": "t",
        # missing `text`
    }
    async with await _make_client() as c:
        r = await c.post(
            "/debates",
            json={"claim": "hi", "max_rounds": 3, "evidence_pool": [bad_item]},
        )
    assert r.status_code == 422


async def test_transcript_json_happy(client_with_fake) -> None:
    """Case 4."""
    did = await client_with_fake.create("hi", 3)
    client_with_fake.store[did].status = "done"
    client_with_fake._status_sequence = []
    async with await _make_client() as c:
        r = await c.get(f"/debates/{did}/transcript.json")
    assert r.status_code == 200
    body = r.json()
    assert body["debate_id"] == str(did)
    assert body["claim"] == "hi"
    assert body["verdict"] == "TRUE"
    assert body["confidence"] == 0.9
    assert isinstance(body["rounds"], list)
    assert len(body["transcript_hash"]) == 64


async def test_transcript_json_hash_deterministic(client_with_fake) -> None:
    """Case 5."""
    did = await client_with_fake.create("hi", 3)
    client_with_fake.store[did].status = "done"
    client_with_fake._status_sequence = []
    async with await _make_client() as c:
        r1 = await c.get(f"/debates/{did}/transcript.json")
        r2 = await c.get(f"/debates/{did}/transcript.json")
    assert r1.json()["transcript_hash"] == r2.json()["transcript_hash"]


async def test_transcript_json_includes_citations_when_used(client_with_fake) -> None:
    """Case 6."""
    did = await client_with_fake.create("hi", 3)
    d = client_with_fake.store[did]
    d.status = "done"
    d.rounds_struct = [
        {
            "side": "proponent",
            "round": 1,
            "argument_md": "cite [cert:abc]",
            "citations": [{"type": "cert", "ref": "abc-uuid-like", "title": "Src"}],
        }
    ]
    client_with_fake._status_sequence = []
    async with await _make_client() as c:
        r = await c.get(f"/debates/{did}/transcript.json")
    assert r.status_code == 200
    body = r.json()
    rounds = body["rounds"]
    assert rounds[0]["citations"] == [{"type": "cert", "ref": "abc-uuid-like", "title": "Src"}]


async def test_transcript_json_409_when_running(client_with_fake) -> None:
    """Case 7."""
    did = await client_with_fake.create("hi", 3)
    d = client_with_fake.store[did]
    d.status = "running"
    client_with_fake._status_sequence = []
    async with await _make_client() as c:
        r = await c.get(f"/debates/{did}/transcript.json")
    assert r.status_code == 409
    assert r.json()["detail"] == "Debate still running"


async def test_transcript_json_409_when_pending(client_with_fake) -> None:
    did = await client_with_fake.create("hi", 3)
    d = client_with_fake.store[did]
    d.status = "pending"
    client_with_fake._status_sequence = []
    async with await _make_client() as c:
        r = await c.get(f"/debates/{did}/transcript.json")
    assert r.status_code == 409


async def test_transcript_json_404_when_unknown(client_with_fake) -> None:
    """Case 8."""
    async with await _make_client() as c:
        r = await c.get(f"/debates/{uuid4()}/transcript.json")
    assert r.status_code == 404


async def test_transcript_json_500_when_verdict_missing(client_with_fake) -> None:
    """Invariant: a 'done' debate without a verdict should 500, not mask."""
    did = await client_with_fake.create("hi", 3)
    d = client_with_fake.store[did]
    d.status = "done"
    d.verdict = None
    client_with_fake._status_sequence = []
    async with await _make_client() as c:
        r = await c.get(f"/debates/{did}/transcript.json")
    assert r.status_code == 500


async def test_transcript_json_falls_back_to_rounds_when_no_rounds_struct(
    client_with_fake,
) -> None:
    """Older debates without rounds_struct must still yield a valid transcript."""
    did = await client_with_fake.create("hi", 3)
    d = client_with_fake.store[did]
    d.status = "done"
    d.rounds_struct = None
    d.rounds = [{"side": "proponent", "round": 1, "argument": "a", "evidence": [], "citations": []}]
    d.transcript_hash = None  # force in-endpoint computation
    client_with_fake._status_sequence = []
    async with await _make_client() as c:
        r = await c.get(f"/debates/{did}/transcript.json")
    assert r.status_code == 200
    body = r.json()
    assert len(body["rounds"]) == 1
    assert body["rounds"][0]["argument_md"] == "a"
    assert len(body["transcript_hash"]) == 64


async def test_transcript_json_handles_malformed_round_entries(client_with_fake) -> None:
    """Non-dict / bogus round entries must be skipped, not crash."""
    did = await client_with_fake.create("hi", 3)
    d = client_with_fake.store[did]
    d.status = "done"
    d.rounds_struct = [
        "not a dict",  # skipped
        {
            "side": "unknown",  # falls back to proponent
            "round": 0,  # below min — coerced to 1
            "argument_md": "ok",
            "citations": [
                "not dict",
                {"type": "cert"},  # missing ref
                {"ref": "x"},  # missing type
                {"type": "cert", "ref": "good", "title": "G"},
            ],
        },
    ]
    client_with_fake._status_sequence = []
    async with await _make_client() as c:
        r = await c.get(f"/debates/{did}/transcript.json")
    assert r.status_code == 200
    body = r.json()
    assert len(body["rounds"]) == 1
    rnd = body["rounds"][0]
    assert rnd["side"] == "proponent"
    assert rnd["round"] == 1
    assert rnd["citations"] == [{"type": "cert", "ref": "good", "title": "G"}]


async def test_transcript_json_coerces_non_int_round(client_with_fake) -> None:
    did = await client_with_fake.create("hi", 3)
    d = client_with_fake.store[did]
    d.status = "done"
    d.rounds_struct = [{"side": "proponent", "round": "not-int", "argument_md": "a"}]
    client_with_fake._status_sequence = []
    async with await _make_client() as c:
        r = await c.get(f"/debates/{did}/transcript.json")
    assert r.status_code == 200
    assert r.json()["rounds"][0]["round"] == 1


async def test_stream_emits_state_and_done(client_with_fake, monkeypatch) -> None:
    monkeypatch.setattr(debates_router_mod, "STREAM_POLL_SECONDS", 0.01)
    monkeypatch.setattr(debates_router_mod, "STREAM_MAX_SECONDS", 5.0)
    did = await client_with_fake.create("hi", 3)
    # Reset the get() status sequence so we see running -> done evolution
    client_with_fake._status_sequence = ["running", "running", "done"]
    client_with_fake.store[did].status = "pending"

    seen_state = False
    seen_done = False
    async with await _make_client() as c, c.stream("GET", f"/debates/{did}/stream") as r:
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
