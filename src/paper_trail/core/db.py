"""Async SQLAlchemy engine + session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


def make_engine() -> Any:
    raise NotImplementedError


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    raise NotImplementedError
    yield  # pragma: no cover
