"""Service layer — orchestrates the graph and persistence."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from paper_trail.agents import graph as graph_mod
from paper_trail.agents.state import initial_state
from paper_trail.core.langfuse import trace


class DebateService:
    def __init__(self, repo: Any) -> None:
        self.repo = repo

    async def create(self, claim: str, max_rounds: int) -> UUID:
        debate = await self.repo.create(claim, max_rounds)
        return debate.id  # type: ignore[no-any-return]

    @trace("debate.run")
    async def run(self, debate_id: UUID) -> Any:
        debate = await self.repo.get(debate_id)
        if debate is None:
            raise ValueError(f"debate {debate_id} not found")
        await self.repo.set_status(debate_id, "running")
        state = initial_state(debate.claim, debate.max_rounds)
        graph = graph_mod.build_graph()
        try:
            result = await graph.ainvoke(state)
        except Exception:
            await self.repo.set_status(debate_id, "error")
            raise
        await self.repo.update_result(
            debate_id,
            verdict=result.get("verdict") or "INCONCLUSIVE",
            confidence=float(result.get("confidence") or 0.0),
            rounds=list(result.get("rounds") or []),
            transcript_md=str(result.get("transcript_md") or ""),
        )
        return await self.repo.get(debate_id)

    async def get(self, debate_id: UUID) -> Any:
        return await self.repo.get(debate_id)

    async def list(self, cursor: str | None, limit: int = 50) -> tuple[list[Any], str | None]:
        return await self.repo.list_page(cursor, limit)  # type: ignore[no-any-return]
