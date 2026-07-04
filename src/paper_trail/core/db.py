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
    """Create (and memoize) the async SQLAlchemy engine.

    On a serverless Postgres (Neon) the proxy drops idle connections and
    scales compute to zero, so a pooled connection reused after an idle
    window is dead — ``pool_pre_ping`` revalidates on checkout and
    ``pool_recycle`` retires connections before Neon's idle timeout (REL-2 /
    P9). The size/overflow/timeout caps bound how many long operations can
    hold connections concurrently (REL-1). These pool args are meaningless
    for sqlite (used in tests), so they are only applied to real databases.
    """
    global _engine, _sessionmaker
    if _engine is None:
        url = settings.database_url
        kwargs: dict[str, Any] = {"future": True}
        if not url.startswith("sqlite"):
            kwargs.update(
                pool_pre_ping=True,
                pool_recycle=300,
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
                pool_timeout=settings.db_pool_timeout,
            )
        _engine = create_async_engine(url, **kwargs)
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
