"""Smoke tests: /health, /version, and /metrics."""

from fastapi.testclient import TestClient

from paper_trail.main import app

client = TestClient(app)


def test_health_ok() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "paper_trail"
    assert body["db"] in {"ok", "down"}
    assert "commit_sha" in body


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
        def instrument(self, app: FastAPI) -> "_StubInstrumentator":
            return self

        def expose(self, app: FastAPI, **kw: object) -> "_StubInstrumentator":
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
