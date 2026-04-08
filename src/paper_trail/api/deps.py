"""FastAPI dependency providers."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from paper_trail.core.db import session_scope
from paper_trail.repositories.debates import DebateRepo
from paper_trail.services.debates import DebateService


async def get_service() -> AsyncGenerator[DebateService, None]:
    """Yield a `DebateService` bound to a freshly opened async session.

    This is the single injection point for the HTTP layer; tests override
    this dependency directly via `app.dependency_overrides`.
    """
    async with session_scope() as session:
        repo = DebateRepo(session)
        yield DebateService(repo)
