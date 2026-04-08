"""Service layer — orchestrates the graph and persistence."""

from __future__ import annotations

from typing import Any
from uuid import UUID


class DebateService:
    def __init__(self, repo: Any) -> None:
        self.repo = repo

    async def create(self, claim: str, max_rounds: int) -> UUID:
        raise NotImplementedError

    async def run(self, debate_id: UUID) -> Any:
        raise NotImplementedError

    async def get(self, debate_id: UUID) -> Any:
        raise NotImplementedError

    async def list(
        self, cursor: str | None, limit: int = 50
    ) -> tuple[list[Any], str | None]:
        raise NotImplementedError
