"""FastAPI dependency providers."""

from __future__ import annotations

from fastapi import HTTPException, Request

from paper_trail.core.db import session_scope
from paper_trail.platform.platform_token import INVALID, MISSING, principal_of
from paper_trail.platform.token_verifier import BASTION_ISSUER, Principal
from paper_trail.services.debates import DebateService


def get_service() -> DebateService:
    """Provide a `DebateService` that opens a short-lived session per operation.

    This is the single injection point for the HTTP layer; tests override this
    dependency directly via `app.dependency_overrides`.

    Unlike the old provider, this does **not** hold a session open for the
    request lifetime: each service call opens/commits/closes its own session
    (via `session_scope`). That keeps long-lived paths — the SSE poll loop and
    the synchronous `/platform/debate` run — from pinning a pooled connection
    for their whole duration (REL-1).
    """
    return DebateService(session_factory=session_scope)


def require_bastion(request: Request) -> Principal:
    """``POST /platform/debate`` is bastion's alone (spec 10 §4).

    The middleware has already verified any token; this only checks who signed
    it. A valid frontend token is refused as invalid here, and an anonymous
    request (``DEMO_MODE``, or the test settings without a key) as missing.
    """
    principal = principal_of(request)
    if principal is None:
        raise HTTPException(status_code=401, detail=MISSING["detail"])
    if principal.iss != BASTION_ISSUER:
        raise HTTPException(status_code=401, detail=INVALID["detail"])
    return principal
