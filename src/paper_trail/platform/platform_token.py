"""X-Platform-Token middleware — contract v2 (docs/specs/10-platform-contract-v2.md).

The imperative shell around ``token_verifier``: it loads the trusted keys from
the environment (bastion's from ``BASTION_SIGNING_KEY_PUBLIC`` or, cached for an
hour, ``BASTION_PUBLIC_KEY_URL``; the frontend's from
``PAPER_TRAIL_FRONTEND_SIGNING_KEY_PUBLIC``), decides per request whether the
route is public, and answers 401 / 503 with the contract's envelopes. A verified
caller is left on ``request.state.platform_principal`` for the rate limiter and
for ``POST /platform/debate``'s bastion-only check.

Per request, in order (spec 10 §3): public route → pass; no trusted key → 503
(the test settings pass as anonymous); no token → 401 missing, or anonymous
with a WARNING under ``DEMO_MODE``; a present token → verified, any failure 401
invalid with a WARNING naming the check, never the token.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import functools
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable

import httpx
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from paper_trail.core.config import settings
from paper_trail.platform.token_verifier import (
    BASTION_ISSUER,
    FRONTEND_ISSUER,
    FRONTEND_ROLE,
    Principal,
    TokenRejectedError,
    TrustedKey,
    verify_token,
)

logger = logging.getLogger(__name__)

_Handler = Callable[[Request], Awaitable[Response]]

_HEADER = "x-platform-token"
BASTION_KEY_ENV = "BASTION_SIGNING_KEY_PUBLIC"
BASTION_KEY_URL_ENV = "BASTION_PUBLIC_KEY_URL"
FRONTEND_KEY_ENV = "PAPER_TRAIL_FRONTEND_SIGNING_KEY_PUBLIC"
_BOM = "﻿"
_PUBLIC_KEY_TTL_S = 3600.0
_TEST_APP_ENV = "test"

# Public routes (spec 10 §3): read-only methods on exact paths, never a write.
_PUBLIC_METHODS = frozenset({"GET", "HEAD"})
_PUBLIC_PATHS = frozenset(
    {
        "/health",
        "/version",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/openapi.json",
        "/metrics",
        "/platform/receipt-public-key",
    }
)
_PUBLIC_TRANSCRIPT = re.compile(r"/debates/[^/]+/transcript\.(?:json|md)")

MISSING = {"detail": "missing_platform_token"}
INVALID = {"detail": "invalid_platform_token"}
UNCONFIGURED = {"detail": "platform_auth_unconfigured"}

# (fetched_at_monotonic, base64 SPKI DER) for the BASTION_PUBLIC_KEY_URL path.
_url_key_cache: tuple[float, str | None] = (0.0, None)


def reset_public_key_cache() -> None:
    """Clear the fetched bastion key and the parsed key values (test seam)."""
    global _url_key_cache
    _url_key_cache = (0.0, None)
    _parse_key_value.cache_clear()


def is_public(method: str, path: str) -> bool:
    """True for the read-only verification surfaces that never need a token."""
    if method not in _PUBLIC_METHODS:
        return False
    return path in _PUBLIC_PATHS or _PUBLIC_TRANSCRIPT.fullmatch(path) is not None


def extract_token(request: Request) -> str | None:
    """``X-Platform-Token`` first, then an ``Authorization: Bearer`` token."""
    header = request.headers.get(_HEADER, "").strip()
    if header:
        return header
    scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and credentials.strip():
        return credentials.strip()
    return None


@functools.lru_cache(maxsize=8)
def _parse_key_value(variable: str, value: str) -> Ed25519PublicKey | None:
    """Parse one configured base64 SPKI DER value; cached so a bad value warns once."""
    try:
        key = serialization.load_der_public_key(base64.b64decode(value, validate=True))
    except (ValueError, binascii.Error, UnsupportedAlgorithm):
        key = None
    if isinstance(key, Ed25519PublicKey):
        return key
    # Name the variable, never its value.
    logger.warning("%s is not a base64 Ed25519 public key (SPKI DER); ignoring it", variable)
    return None


def _clean(value: str) -> str:
    """Strip a byte-order mark and edge whitespace (values pasted from a Windows shell)."""
    return value.strip().lstrip(_BOM).strip()


def _key_from_env(variable: str) -> Ed25519PublicKey | None:
    value = _clean(os.environ.get(variable, ""))
    return _parse_key_value(variable, value) if value else None


def _fetch_bastion_key(url: str) -> str | None:
    """Blocking fetch of bastion's public key (run off the event loop)."""
    global _url_key_cache
    try:
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        value = _clean(str(resp.json()["publicKey"]))
    except Exception:  # pragma: no cover - network failure path
        logger.warning("could not fetch bastion's public key from %s", BASTION_KEY_URL_ENV)
        return None
    _url_key_cache = (time.monotonic(), value)
    return value


async def _bastion_key() -> Ed25519PublicKey | None:
    """bastion's key: the env value, else the URL-fetched one (cached an hour)."""
    if _clean(os.environ.get(BASTION_KEY_ENV, "")):
        return _key_from_env(BASTION_KEY_ENV)
    url = os.environ.get(BASTION_KEY_URL_ENV, "").strip()
    if not url:
        return None
    fetched_at, value = _url_key_cache
    if value is None or time.monotonic() - fetched_at >= _PUBLIC_KEY_TTL_S:
        value = await asyncio.to_thread(_fetch_bastion_key, url)
    return _parse_key_value(BASTION_KEY_URL_ENV, value) if value else None


async def load_trusted_keys() -> tuple[TrustedKey, ...]:
    """Every configured key with the issuer (and role) a token it verifies must claim."""
    keys: list[TrustedKey] = []
    bastion = await _bastion_key()
    if bastion is not None:
        keys.append(TrustedKey(key=bastion, issuer=BASTION_ISSUER, required_role=None))
    frontend = _key_from_env(FRONTEND_KEY_ENV)
    if frontend is not None:
        keys.append(TrustedKey(key=frontend, issuer=FRONTEND_ISSUER, required_role=FRONTEND_ROLE))
    return tuple(keys)


def principal_of(request: Request) -> Principal | None:
    """The verified caller the middleware left on ``request``, if any."""
    principal = getattr(request.state, "platform_principal", None)
    return principal if isinstance(principal, Principal) else None


def install_platform_token(app: FastAPI) -> None:
    """Attach the contract-v2 ``X-Platform-Token`` middleware to ``app``."""

    @app.middleware("http")
    async def _platform_token_middleware(request: Request, call_next: _Handler) -> Response:
        if is_public(request.method, request.url.path):
            return await call_next(request)

        keys = await load_trusted_keys()
        if not keys:
            if settings.app_env == _TEST_APP_ENV:
                return await call_next(request)
            logger.error("no trusted platform key is configured; failing closed")
            return JSONResponse(UNCONFIGURED, status_code=503)

        token = extract_token(request)
        if token is None:
            if settings.demo_mode:
                logger.warning(
                    "DEMO_MODE: %s %s has no platform token; serving it anonymously",
                    request.method,
                    request.url.path,
                )
                return await call_next(request)
            return JSONResponse(MISSING, status_code=401)

        try:
            principal = verify_token(token, keys, now=time.time())
        except TokenRejectedError as rejected:
            logger.warning(
                "platform token rejected on %s %s: failed check %r",
                request.method,
                request.url.path,
                rejected.check,
            )
            return JSONResponse(INVALID, status_code=401)
        request.state.platform_principal = principal
        return await call_next(request)
