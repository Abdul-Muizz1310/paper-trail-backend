"""Async SQLAlchemy engine + session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from paper_trail.core.config import settings


def _make_engine() -> Any:  # type: ignore[no-untyped-def]
    raise NotImplementedError


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    raise NotImplementedError
    yield  # type: ignore[unreachable]
