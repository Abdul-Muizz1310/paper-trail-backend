"""Unit tests for core/rate_limit.py."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from paper_trail.core import rate_limit
from paper_trail.core.config import settings


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
    request = SimpleNamespace(client=SimpleNamespace(host="1.2.3.4"))
    await dep(request)  # type: ignore[arg-type]
    with pytest.raises(HTTPException):
        await dep(request)  # type: ignore[arg-type]


async def test_dependency_handles_missing_client(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_max_requests", 5)
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: FakeRedis())
    dep = rate_limit.rate_limiter("debates")
    await dep(SimpleNamespace(client=None))  # type: ignore[arg-type]


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
