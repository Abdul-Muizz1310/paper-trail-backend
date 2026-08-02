"""Post-deploy smoke tier — probes a *live* deployment.

Runs only when ``PAPER_TRAIL_BASE_URL`` is set (see ``live_target``); otherwise
every test here skips, so CI stays green while the host is suspended or the
secret is unconfigured. Marked ``smoke`` and deselected from the fast tier.

What it is for: catching the class of failure unit tests structurally cannot —
the container booted but cannot reach Postgres, the wrong image is live, or the
process starts and immediately 502s.
"""

from __future__ import annotations

import time

import httpx
import pytest

from tests.smoke import live_target

pytestmark = pytest.mark.smoke

# The free plan spins down when idle; a cold start has been observed to exceed
# 25s, so the first probe retries instead of failing the deploy gate outright.
_TOTAL_TIMEOUT_S = 120.0
_PER_REQUEST_TIMEOUT_S = 30.0
_RETRY_SLEEP_S = 5.0


@pytest.fixture(scope="module")
def base_url() -> str:
    url = live_target.base_url()
    if url is None:
        pytest.skip(f"{live_target.BASE_URL_ENV} unset — live smoke tier skipped")
    return url


def _get_with_cold_start_retry(url: str) -> httpx.Response:
    """GET ``url``, tolerating cold-start timeouts/5xx until the budget runs out."""
    deadline = time.monotonic() + _TOTAL_TIMEOUT_S
    last_error: str = "no attempt made"
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=_PER_REQUEST_TIMEOUT_S)
        except httpx.HTTPError as exc:  # transport error / read timeout
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if resp.status_code < 500:
                return resp
            last_error = f"HTTP {resp.status_code}"
        time.sleep(_RETRY_SLEEP_S)
    pytest.fail(f"{url} never became reachable within {_TOTAL_TIMEOUT_S:.0f}s ({last_error})")


def test_live_health_reports_ok_and_a_reachable_database(base_url: str) -> None:
    resp = _get_with_cold_start_retry(f"{base_url}/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "paper_trail"
    # /health always answers 200; the DB verdict lives in the payload, so a
    # smoke gate has to assert on the field, not on the status code.
    assert body["db"] == "ok", f"live deployment cannot reach Postgres: {body}"


def test_live_version_reports_a_commit_sha(base_url: str) -> None:
    resp = _get_with_cold_start_retry(f"{base_url}/version")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["service"] == "paper_trail"
    assert body["version"]
    assert body["commit_sha"] != "unknown", "deployed image exposes no commit SHA"
