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
