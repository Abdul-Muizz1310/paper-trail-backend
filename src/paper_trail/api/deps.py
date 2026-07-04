"""FastAPI dependency providers."""

from __future__ import annotations

from paper_trail.core.db import session_scope
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
