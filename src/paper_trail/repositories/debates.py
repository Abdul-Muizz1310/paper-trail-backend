"""Async SQLAlchemy repository for debates."""

from __future__ import annotations

from typing import Any
from uuid import UUID


class DebateRepo:
    def __init__(self, session: Any) -> None:
        self.session = session

    async def create(self, claim: str, max_rounds: int) -> Any:
        raise NotImplementedError

    async def get(self, debate_id: UUID) -> Any:
        raise NotImplementedError

    async def list(
        self, cursor: str | None, limit: int = 50
    ) -> tuple[list[Any], str | None]:
        raise NotImplementedError

    async def update_result(
        self,
        debate_id: UUID,
        verdict: str,
        confidence: float,
        rounds: list[dict[str, Any]],
        transcript_md: str,
    ) -> None:
        raise NotImplementedError

    async def set_status(self, debate_id: UUID, status: str) -> None:
        raise NotImplementedError
