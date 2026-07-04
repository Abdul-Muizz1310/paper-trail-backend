"""X-Platform-Token middleware — verifies bastion-minted Ed25519 platform JWTs.

In demo mode (the default for this deployment) the middleware accepts every
request and logs a one-time warning. When demo mode is off AND a bastion public
key is configured — either ``BASTION_SIGNING_KEY_PUBLIC`` (base64 DER) or fetched
once from ``BASTION_PUBLIC_KEY_URL`` and cached for an hour — it verifies the
EdDSA-signed ``X-Platform-Token`` on every non-exempt route and rejects invalid
or missing tokens with 401. The platform and docs endpoints are always exempt.

Enforcement is opt-in: with no key configured the middleware fails open, since
bastion is the only minter and frontends call this service directly in the
demo deployment. Token format is bastion's ``{sub, role, service, iat, exp}``
EdDSA JWT (see bastion ``src/lib/gateway/jwt.ts``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable

import httpx
import jwt
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

_Handler = Callable[[Request], Awaitable[Response]]

_HEADER = "x-platform-token"
_EXEMPT_EXACT = frozenset(
    {
        "/health",
        "/version",
        "/metrics",
        "/openapi.json",
        # The receipt verification key must be publicly fetchable so third
        # parties can verify signed transcripts even under token enforcement.
        "/platform/receipt-public-key",
    }
)
_EXEMPT_PREFIXES = ("/docs", "/redoc")
_PUBLIC_KEY_TTL_S = 3600.0

# (fetched_at_monotonic, pem) — only used for the BASTION_PUBLIC_KEY_URL path.
_key_cache: tuple[float, str | None] = (0.0, None)


def reset_public_key_cache() -> None:
    """Clear the fetched-public-key cache (test seam)."""
    global _key_cache
    _key_cache = (0.0, None)


def _wrap_pem(b64_der: str) -> str:
    return f"-----BEGIN PUBLIC KEY-----\n{b64_der.strip()}\n-----END PUBLIC KEY-----\n"


def _cached_url_key() -> str | None:
    """Return the cached URL-fetched PEM if still fresh, else None."""
    cached_at, cached = _key_cache
    if cached is not None and (time.monotonic() - cached_at) < _PUBLIC_KEY_TTL_S:
        return cached
    return None


def _fetch_and_cache_url_key(url: str) -> str | None:
    """Blocking fetch of the public key from `url` (run off the event loop)."""
    global _key_cache
    try:
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        pem = _wrap_pem(str(resp.json()["publicKey"]))
    except Exception:  # pragma: no cover - network failure path
        logger.warning("could not fetch bastion public key from %s", url)
        return None
    _key_cache = (time.monotonic(), pem)
    return pem


def load_public_key_pem() -> str | None:
    """Resolve bastion's Ed25519 public key as PEM (env first, then cached URL).

    Synchronous variant — safe off the event loop (e.g. inside asyncio.to_thread
    or from a sync test). Prefer ``load_public_key_pem_async`` on the loop.
    """
    raw = os.environ.get("BASTION_SIGNING_KEY_PUBLIC")
    if raw:
        return _wrap_pem(raw)
    url = os.environ.get("BASTION_PUBLIC_KEY_URL")
    if not url:
        return None
    cached = _cached_url_key()
    if cached is not None:
        return cached
    return _fetch_and_cache_url_key(url)


async def load_public_key_pem_async() -> str | None:
    """Async key resolver that never blocks the event loop.

    The env-var key is instant; the URL fetch (only on a cold cache) is pushed
    through ``asyncio.to_thread`` so a slow/hanging bastion never stalls other
    in-flight requests on a single-worker deploy.
    """
    raw = os.environ.get("BASTION_SIGNING_KEY_PUBLIC")
    if raw:
        return _wrap_pem(raw)
    url = os.environ.get("BASTION_PUBLIC_KEY_URL")
    if not url:
        return None
    cached = _cached_url_key()
    if cached is not None:
        return cached
    return await asyncio.to_thread(_fetch_and_cache_url_key, url)


def verify_platform_jwt(token: str) -> bool:
    """Verify an EdDSA platform JWT against bastion's public key.

    Fails **closed**: returns False when no key is configured (so a
    non-demo deployment with no key rejects everything) or verification fails.
    """
    pem = load_public_key_pem()
    if pem is None:
        return False
    return _verify(token, pem)


def _is_exempt(path: str) -> bool:
    return path in _EXEMPT_EXACT or path.startswith(_EXEMPT_PREFIXES)


def _verify(token: str, pem: str) -> bool:
    try:
        jwt.decode(token, pem, algorithms=["EdDSA"])
    except Exception:
        return False
    return True


def install_platform_token(app: FastAPI, *, demo_mode: bool) -> None:
    """Attach the X-Platform-Token verification middleware to ``app``."""
    if demo_mode:
        logger.warning(
            "DEMO_MODE active: X-Platform-Token validation is bypassed; accepting all requests"
        )

    @app.middleware("http")
    async def _platform_token_middleware(request: Request, call_next: _Handler) -> Response:
        if demo_mode or _is_exempt(request.url.path):
            return await call_next(request)
        pem = await load_public_key_pem_async()
        if pem is None:
            # Enforcement is opt-in; with no key configured we fail open.
            return await call_next(request)
        token = request.headers.get(_HEADER)
        if not token or not _verify(token, pem):
            return JSONResponse({"error": "invalid platform token"}, status_code=401)
        return await call_next(request)
