"""Redis-backed fixed-window rate limiting for the paid debate endpoints.

Both `POST /debates` and `POST /platform/debate` fan out to real OpenRouter +
Tavily calls, so without a throttle a handful of scripted requests can burn the
owner's budget. Upstash Redis is already provisioned in `render.yaml`; this
module wires it to a per-identifier fixed-window counter.

Fail-open by design: when rate limiting is disabled (the default for
local/dev/CI) or the Upstash backend errors, requests are allowed. It never
becomes a new single point of failure in front of the app.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request, status

from paper_trail.core.config import settings

logger = logging.getLogger(__name__)

_redis: Any | None = None
_redis_initialized = False


def reset() -> None:
    """Clear the memoized client (test seam)."""
    global _redis, _redis_initialized
    _redis = None
    _redis_initialized = False


def _get_redis() -> Any | None:
    """Lazily build the async Upstash client, or None when unavailable."""
    global _redis, _redis_initialized
    if _redis_initialized:
        return _redis
    _redis_initialized = True
    if not (settings.upstash_redis_rest_url and settings.upstash_redis_rest_token):
        _redis = None
        return None
    try:
        from upstash_redis.asyncio import Redis

        _redis = Redis(
            url=settings.upstash_redis_rest_url,
            token=settings.upstash_redis_rest_token,
        )
    except Exception:  # pragma: no cover - defensive: bad creds / import
        logger.warning("could not initialize Upstash rate-limit client; limiter disabled")
        _redis = None
    return _redis


async def enforce_rate_limit(identifier: str, *, scope: str) -> None:
    """Increment the window counter for `identifier`; raise 429 when exceeded.

    No-op when rate limiting is disabled or the backend is unreachable.
    """
    if not settings.rate_limit_enabled:
        return
    redis = _get_redis()
    if redis is None:
        return
    key = f"paper-trail:ratelimit:{scope}:{identifier}"
    try:
        count = int(await redis.incr(key))
        if count == 1:
            await redis.expire(key, settings.rate_limit_window_s)
    except Exception:  # pragma: no cover - network/backend failure
        logger.warning("rate-limit backend error; allowing request", exc_info=True)
        return
    if count > settings.rate_limit_max_requests:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
        )


def client_identifier(request: Request) -> str:
    """Resolve the per-caller throttle key for `request`.

    In production this app sits behind Render's edge proxy, so the TCP peer
    (``request.client.host``) is the *proxy*, identical for every caller — keying
    on it alone collapses the whole internet into one bucket, which is not a
    throttle at all. So prefer the forwarded originating address:

    1. leftmost entry of ``X-Forwarded-For`` (the client the edge saw),
    2. ``X-Real-IP``,
    3. the socket peer,
    4. ``"unknown"`` — never an empty string, which would be a shared bucket.

    Trade-off, stated plainly: forwarded headers are client-supplied, so a
    determined caller can rotate them to get fresh buckets. That is acceptable
    here because the limiter exists to cap accidental/scripted spend on the
    OpenRouter + Tavily fan-out, not to stop a motivated attacker, and the
    alternative (one global bucket) fails every honest caller instead.
    """
    for header in ("x-forwarded-for", "x-real-ip"):
        raw = request.headers.get(header)
        if not raw:
            continue
        for candidate in raw.split(","):
            cleaned = candidate.strip()
            if cleaned:
                return cleaned
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def rate_limiter(scope: str) -> Any:
    """Build a FastAPI dependency that throttles by client IP within `scope`."""

    async def _dependency(request: Request) -> None:
        await enforce_rate_limit(client_identifier(request), scope=scope)

    return _dependency
