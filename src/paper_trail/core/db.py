"""Async SQLAlchemy engine + session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from paper_trail.core.config import settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def make_engine() -> Any:
    """Create (and memoize) the async SQLAlchemy engine."""
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(settings.database_url, future=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Yield an async session with automatic commit/rollback."""
    make_engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
