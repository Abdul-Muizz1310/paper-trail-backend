"""X-Platform-Token middleware — verifies Ed25519 platform JWTs from two minters.

In demo mode the middleware accepts every request and logs a one-time warning.
When demo mode is off it trusts two keys (docs/specs/09-platform-token.md):

- bastion's — ``BASTION_SIGNING_KEY_PUBLIC`` (base64 DER) or fetched once from
  ``BASTION_PUBLIC_KEY_URL`` and cached for an hour;
- paper-trail-frontend's own — ``PAPER_TRAIL_FRONTEND_SIGNING_KEY_PUBLIC``
  (base64 DER, env only). A token signed by it must also carry
  ``service == "paper-trail"`` and ``role == "frontend"`` (DR-0030: the
  frontend's server proxies browser calls).

With either key configured it verifies the EdDSA-signed ``X-Platform-Token``
(``exp`` required and in the future, 10 s clock-skew leeway on ``exp`` and
``iat``) on every non-exempt route and rejects
invalid or missing tokens with 401. The platform and docs endpoints are always
exempt. With no key configured the middleware fails open: enforcement is
opt-in. Token format is ``{sub, role, service, iat, exp}`` EdDSA JWT (see
bastion ``src/lib/gateway/jwt.ts``).

``verify_platform_jwt`` (the ``POST /platform/debate`` bearer check) trusts
bastion's key only and fails closed without it: the frontend key never opens
bastion's integrated-demo entry point.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import jwt
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
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

# A frontend-key token is honoured only for this service and this role.
_SERVICE_ID = "paper-trail"
_FRONTEND_ROLE = "frontend"
_FRONTEND_KEY_ENV = "PAPER_TRAIL_FRONTEND_SIGNING_KEY_PUBLIC"
_BOM = "\ufeff"
# Tokens are minted on Vercel/bastion and verified here on Render; tolerate this
# much clock skew on ``exp`` and ``iat`` (PyJWT rejects an ``iat`` in the future).
_LEEWAY_S = 10

# (fetched_at_monotonic, pem) — only used for the BASTION_PUBLIC_KEY_URL path.
_key_cache: tuple[float, str | None] = (0.0, None)


def reset_public_key_cache() -> None:
    """Clear the fetched-public-key cache and the parsed frontend key (test seam)."""
    global _key_cache
    _key_cache = (0.0, None)
    _frontend_key_from_value.cache_clear()


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
    bastion's key only — a frontend-key token is never accepted here.
    """
    pem = load_public_key_pem()
    if pem is None:
        return False
    return _verify(token, pem)


def parse_ed25519_public_key(b64_der: str) -> Ed25519PublicKey | None:
    """Parse base64 SPKI DER into an Ed25519 public key; ``None`` for anything else."""
    try:
        key = serialization.load_der_public_key(base64.b64decode(b64_der, validate=True))
    except (ValueError, UnsupportedAlgorithm):
        return None
    return key if isinstance(key, Ed25519PublicKey) else None


@functools.lru_cache(maxsize=4)
def _frontend_key_from_value(value: str) -> Ed25519PublicKey | None:
    """Parse one configured value; cached so a bad value warns once, not per request."""
    key = parse_ed25519_public_key(value)
    if key is None:
        # Name the variable, never its value.
        logger.warning(
            "%s is not a base64 Ed25519 public key (SPKI DER); ignoring it", _FRONTEND_KEY_ENV
        )
    return key


def load_frontend_public_key() -> Ed25519PublicKey | None:
    """Resolve paper-trail-frontend's Ed25519 public key from its env var (no URL fetch).

    A byte-order mark and edge whitespace are stripped (values pasted from a
    Windows shell carry them). Empty means absent; a malformed value logs one
    warning and is treated as absent, so it can never crash a request.
    """
    value = os.environ.get(_FRONTEND_KEY_ENV, "").strip().lstrip(_BOM).strip()
    if not value:
        return None
    return _frontend_key_from_value(value)


def _is_exempt(path: str) -> bool:
    return path in _EXEMPT_EXACT or path.startswith(_EXEMPT_PREFIXES)


def _decode(token: str, key: str | Ed25519PublicKey) -> dict[str, Any] | None:
    """EdDSA-verify ``token`` against ``key`` with ``exp`` required and live (± leeway)."""
    try:
        return jwt.decode(
            token,
            key,
            algorithms=["EdDSA"],
            options={"require": ["exp"]},
            leeway=_LEEWAY_S,
        )
    except Exception:
        # PyJWT raises its own errors for bad tokens, and ValueError/TypeError for
        # some malformed keys and claim values; every one of them is a rejection.
        return None


def _verify(token: str, pem: str) -> bool:
    return _decode(token, pem) is not None


def _verify_frontend(token: str, key: Ed25519PublicKey) -> bool:
    claims = _decode(token, key)
    return (
        claims is not None
        and claims.get("service") == _SERVICE_ID
        and claims.get("role") == _FRONTEND_ROLE
    )


def _token_is_trusted(
    token: str, *, bastion_pem: str | None, frontend_key: Ed25519PublicKey | None
) -> bool:
    """True when bastion's key verifies ``token``, or the frontend key does for this service."""
    if bastion_pem is not None and _verify(token, bastion_pem):
        return True
    return frontend_key is not None and _verify_frontend(token, frontend_key)


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
        frontend_key = load_frontend_public_key()
        if pem is None and frontend_key is None:
            # Enforcement is opt-in; with no key configured we fail open.
            return await call_next(request)
        token = request.headers.get(_HEADER)
        if not token or not _token_is_trusted(token, bastion_pem=pem, frontend_key=frontend_key):
            return JSONResponse({"error": "invalid platform token"}, status_code=401)
        return await call_next(request)
