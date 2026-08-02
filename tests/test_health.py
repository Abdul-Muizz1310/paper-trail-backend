"""Unit tests: /health, /version, and /metrics.

The DB probe is stubbed in both directions on purpose. Two reasons:

- ``settings`` is module-level and reads ``.env``, so an un-stubbed ``/health``
  call opens a real connection to whatever ``DATABASE_URL`` is configured — on a
  developer machine that is the live Neon branch, which makes the "fully mocked"
  unit tier quietly network-dependent.
- Asserting ``db in {"ok", "down"}`` is vacuously true: it passes even if the
  probe is completely broken. Each direction is pinned separately instead.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from paper_trail.main import app
from paper_trail.platform import health as health_mod

client = TestClient(app)


class _RecordingSession:
    """Minimal async session that records the SQL it was asked to execute."""

    def __init__(self, executed: list[str]) -> None:
        self._executed = executed

    async def execute(self, statement: Any) -> None:
        self._executed.append(str(statement))


def _healthy_scope(executed: list[str]) -> Callable[[], Any]:
    @asynccontextmanager
    async def _scope() -> AsyncIterator[_RecordingSession]:
        yield _RecordingSession(executed)

    return _scope


def _broken_scope(exc: Exception) -> Callable[[], Any]:
    @asynccontextmanager
    async def _scope() -> AsyncIterator[Any]:
        raise exc
        yield  # pragma: no cover - unreachable, keeps this a generator

    return _scope


def test_health_reports_db_ok_and_actually_probes_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[str] = []
    monkeypatch.setattr(health_mod, "session_scope", _healthy_scope(executed))

    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "paper_trail"
    assert body["db"] == "ok"
    assert "commit_sha" in body
    # The verdict must come from a real round trip, not a hardcoded literal.
    assert executed == ["SELECT 1"]


def test_health_reports_db_down_when_the_probe_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken database must surface as db="down" — never as a crash or "ok"."""
    monkeypatch.setattr(
        health_mod, "session_scope", _broken_scope(RuntimeError("connection refused"))
    )

    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    # /health stays 200 by design (Render's healthCheckPath uses it, and a
    # transient DB blip should not trigger a redeploy loop); the DB verdict is
    # carried in the payload instead.
    assert body["status"] == "ok"
    assert body["db"] == "down"


def test_version_ok() -> None:
    resp = client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "paper_trail"
    assert "version" in body
    assert "commit_sha" in body


def test_metrics_ok() -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "# HELP" in resp.text or "http_request" in resp.text


def test_metrics_gated_by_token(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """SEC-1: with METRICS_TOKEN set, /metrics requires a matching bearer."""
    from fastapi import FastAPI

    from paper_trail.core.config import settings
    from paper_trail.platform import metrics as metrics_mod

    # Stub the instrumentator so we don't double-register default metrics on a
    # second app (the module-level app already instrumented the global registry).
    class _StubInstrumentator:
        def instrument(self, app: FastAPI) -> _StubInstrumentator:
            return self

        def expose(self, app: FastAPI, **kw: object) -> _StubInstrumentator:
            return self

    monkeypatch.setattr(metrics_mod, "Instrumentator", _StubInstrumentator)
    monkeypatch.setattr(settings, "metrics_token", "s3cret")

    gated = FastAPI()
    metrics_mod.install_metrics(gated)
    gc = TestClient(gated)

    assert gc.get("/metrics").status_code == 401
    assert gc.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = gc.get("/metrics", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
