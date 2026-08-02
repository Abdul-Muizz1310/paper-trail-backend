"""Unit tests for core/rate_limit.py."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from paper_trail.core import rate_limit
from paper_trail.core.config import settings


def _request(
    headers: dict[str, str] | None = None,
    client: tuple[str, int] | None = ("10.0.0.1", 51234),
) -> Request:
    """Build a real Starlette Request so header handling is exercised for real."""
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/debates",
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
            "client": client,
        }
    )


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.expired: list[str] = []

    async def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.expired.append(key)
        return True


@pytest.fixture(autouse=True)
def _reset() -> None:
    rate_limit.reset()


async def test_disabled_is_noop(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: FakeRedis())
    # Even with a backend available, disabled means no enforcement.
    await rate_limit.enforce_rate_limit("ip", scope="debates")


async def test_no_backend_is_noop(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: None)
    await rate_limit.enforce_rate_limit("ip", scope="debates")


async def test_allows_under_limit_then_429(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_max_requests", 2)
    monkeypatch.setattr(settings, "rate_limit_window_s", 60)
    fake = FakeRedis()
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: fake)

    await rate_limit.enforce_rate_limit("ip1", scope="debates")
    await rate_limit.enforce_rate_limit("ip1", scope="debates")
    with pytest.raises(HTTPException) as ei:
        await rate_limit.enforce_rate_limit("ip1", scope="debates")
    assert ei.value.status_code == 429
    # Expiry set exactly once (on the first hit in the window).
    assert fake.expired == ["paper-trail:ratelimit:debates:ip1"]


async def test_separate_identifiers_isolated(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_max_requests", 1)
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: FakeRedis())
    await rate_limit.enforce_rate_limit("a", scope="debates")
    await rate_limit.enforce_rate_limit("b", scope="debates")  # different key, ok


async def test_backend_error_fails_open(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "rate_limit_enabled", True)

    class BoomRedis:
        async def incr(self, key: str) -> int:
            raise RuntimeError("upstash down")

    monkeypatch.setattr(rate_limit, "_get_redis", lambda: BoomRedis())
    await rate_limit.enforce_rate_limit("ip", scope="debates")  # no raise


async def test_dependency_uses_client_host(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_max_requests", 1)
    fake = FakeRedis()
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: fake)
    dep = rate_limit.rate_limiter("debates")
    request = _request(client=("1.2.3.4", 4444))
    await dep(request)
    with pytest.raises(HTTPException):
        await dep(request)
    assert fake.expired == ["paper-trail:ratelimit:debates:1.2.3.4"]


async def test_dependency_handles_missing_client(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_max_requests", 5)
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: FakeRedis())
    dep = rate_limit.rate_limiter("debates")
    await dep(_request(client=None))


# --------------------------------------------------------------------------
# Forwarded-header handling (SEC): behind Render's edge proxy every request
# arrives from the *proxy* socket, so keying purely on request.client.host puts
# the whole internet into a single bucket — the throttle silently stops being
# per-caller and one scripted client can lock everyone out (or, with a generous
# limit, nobody is limited at all).
# --------------------------------------------------------------------------


def test_client_identifier_prefers_forwarded_for_originating_client() -> None:
    req = _request(
        {"x-forwarded-for": "203.0.113.5, 70.41.3.18, 150.172.238.178"},
        client=("10.0.0.1", 51234),
    )
    assert rate_limit.client_identifier(req) == "203.0.113.5"


def test_client_identifier_tolerates_padding_and_empty_entries() -> None:
    req = _request({"x-forwarded-for": " ,  198.51.100.7 , 10.0.0.1 "})
    assert rate_limit.client_identifier(req) == "198.51.100.7"


def test_client_identifier_falls_back_to_x_real_ip() -> None:
    req = _request({"x-real-ip": "198.51.100.42"}, client=("10.0.0.1", 1))
    assert rate_limit.client_identifier(req) == "198.51.100.42"


def test_client_identifier_falls_back_to_socket_peer() -> None:
    assert rate_limit.client_identifier(_request(client=("192.0.2.9", 1))) == "192.0.2.9"


def test_client_identifier_falls_back_to_unknown_without_peer() -> None:
    assert rate_limit.client_identifier(_request(client=None)) == "unknown"


def test_client_identifier_ignores_blank_forwarded_header() -> None:
    """An empty header must not produce an empty bucket key shared by everyone."""
    req = _request({"x-forwarded-for": "   ", "x-real-ip": ""}, client=("192.0.2.9", 1))
    assert rate_limit.client_identifier(req) == "192.0.2.9"


async def test_two_clients_behind_one_proxy_get_separate_buckets(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The actual production bug: same proxy socket, different real callers."""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_max_requests", 1)
    fake = FakeRedis()
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: fake)
    dep = rate_limit.rate_limiter("debates")

    proxy = ("10.0.0.1", 51234)
    alice = _request({"x-forwarded-for": "203.0.113.5"}, client=proxy)
    bob = _request({"x-forwarded-for": "203.0.113.9"}, client=proxy)

    await dep(alice)
    await dep(bob)  # must NOT be throttled by Alice's request
    with pytest.raises(HTTPException) as ei:
        await dep(alice)
    assert ei.value.status_code == 429
    assert sorted(fake.store) == [
        "paper-trail:ratelimit:debates:203.0.113.5",
        "paper-trail:ratelimit:debates:203.0.113.9",
    ]


def test_get_redis_none_when_unconfigured(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "upstash_redis_rest_url", "")
    monkeypatch.setattr(settings, "upstash_redis_rest_token", "")
    rate_limit.reset()
    assert rate_limit._get_redis() is None


def test_get_redis_builds_client_when_configured(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "upstash_redis_rest_url", "https://example.upstash.io")
    monkeypatch.setattr(settings, "upstash_redis_rest_token", "token")
    rate_limit.reset()
    client = rate_limit._get_redis()
    assert client is not None
    # Memoized: second call returns the same instance.
    assert rate_limit._get_redis() is client
    rate_limit.reset()
