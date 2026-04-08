"""Unit tests for api/routers/platform.py."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from paper_trail.api import deps
from paper_trail.core import platform_auth
from paper_trail.main import app


class FakePlatformDebate:
    def __init__(self, id: UUID, claim: str, max_rounds: int) -> None:
        self.id = id
        self.claim = claim
        self.max_rounds = max_rounds
        self.status = "done"
        self.verdict = "TRUE"
        self.confidence = 0.87
        self.rounds: list[dict[str, Any]] = [
            {"side": "proponent", "round": 1, "argument": "a", "evidence": []},
            {"side": "skeptic", "round": 1, "argument": "b", "evidence": []},
        ]
        self.transcript_md = "# t"
        from datetime import datetime

        self.created_at = datetime.utcnow()


class FakePlatformService:
    def __init__(self) -> None:
        self.store: dict[UUID, FakePlatformDebate] = {}
        self.create_called_with: list[tuple[str, int]] = []

    async def create(self, claim: str, max_rounds: int) -> UUID:
        self.create_called_with.append((claim, max_rounds))
        d = FakePlatformDebate(uuid4(), claim, max_rounds)
        self.store[d.id] = d
        return d.id

    async def run(self, debate_id: UUID) -> Any:
        return self.store[debate_id]

    async def get(self, debate_id: UUID) -> Any:
        return self.store.get(debate_id)

    async def list(self, cursor: str | None, limit: int = 50) -> tuple[list[Any], str | None]:
        return list(self.store.values()), None


@pytest.fixture
def fake_service():
    svc = FakePlatformService()

    async def _override():
        yield svc

    app.dependency_overrides[deps.get_service] = _override
    yield svc
    app.dependency_overrides.clear()


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_platform_happy(fake_service, monkeypatch) -> None:
    monkeypatch.setattr(platform_auth.settings, "demo_mode", True)
    async with await _client() as c:
        r = await c.post(
            "/platform/debate",
            json={"claim": "hello", "max_rounds": 3},
            headers={"Authorization": "Bearer demo"},
        )
    assert r.status_code == 200
    body = r.json()
    assert UUID(body["debate_id"])
    assert body["verdict"] == "TRUE"
    assert isinstance(body["rounds_run"], int)
    assert body["transcript_url"].endswith("/transcript.md")


async def test_platform_missing_auth_header_401(fake_service, monkeypatch) -> None:
    monkeypatch.setattr(platform_auth.settings, "demo_mode", False)
    async with await _client() as c:
        r = await c.post("/platform/debate", json={"claim": "x"})
    assert r.status_code == 401


async def test_platform_invalid_token_401(fake_service, monkeypatch) -> None:
    monkeypatch.setattr(platform_auth.settings, "demo_mode", False)
    async with await _client() as c:
        r = await c.post(
            "/platform/debate",
            json={"claim": "x"},
            headers={"Authorization": "Bearer bad"},
        )
    assert r.status_code == 401


async def test_platform_caps_max_rounds_to_three(fake_service, monkeypatch) -> None:
    monkeypatch.setattr(platform_auth.settings, "demo_mode", True)
    async with await _client() as c:
        r = await c.post(
            "/platform/debate",
            json={"claim": "x", "max_rounds": 10},
            headers={"Authorization": "Bearer demo"},
        )
    # Pydantic will reject 10 (le=10 actually allows 10) -- spec says cap, but the schema
    # constrains [1,10]; we then clamp to 3.
    assert r.status_code == 200
    assert fake_service.create_called_with[0][1] == 3


async def test_platform_default_max_rounds_three(fake_service, monkeypatch) -> None:
    monkeypatch.setattr(platform_auth.settings, "demo_mode", True)
    async with await _client() as c:
        r = await c.post(
            "/platform/debate",
            json={"claim": "x"},
            headers={"Authorization": "Bearer demo"},
        )
    assert r.status_code == 200
    assert fake_service.create_called_with[0][1] == 3


async def test_platform_transcript_url_shape(fake_service, monkeypatch) -> None:
    monkeypatch.setattr(platform_auth.settings, "demo_mode", True)
    async with await _client() as c:
        r = await c.post(
            "/platform/debate",
            json={"claim": "x"},
            headers={"Authorization": "Bearer demo"},
        )
    body = r.json()
    assert body["transcript_url"] == f"/debates/{body['debate_id']}/transcript.md"
