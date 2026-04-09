"""Service layer — orchestrates the graph and persistence."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from paper_trail.agents import graph as graph_mod
from paper_trail.agents.state import initial_state
from paper_trail.core.config import settings
from paper_trail.core.langfuse import span, update_current_trace


class DebateService:
    def __init__(self, repo: Any) -> None:
        self.repo = repo

    async def create(self, claim: str, max_rounds: int) -> UUID:
        debate = await self.repo.create(claim, max_rounds)
        return debate.id  # type: ignore[no-any-return]

    async def run(self, debate_id: UUID) -> Any:
        debate = await self.repo.get(debate_id)
        if debate is None:
            raise ValueError(f"debate {debate_id} not found")

        trace_input = {
            "debate_id": str(debate_id),
            "claim": debate.claim,
            "max_rounds": debate.max_rounds,
        }
        trace_metadata = {
            "debate_id": str(debate_id),
            "model_primary": settings.openrouter_model_primary,
            "model_fast": settings.openrouter_model_fast,
            "model_fallback": settings.openrouter_model_fallback,
            "max_rounds": debate.max_rounds,
            "claim_length": len(debate.claim or ""),
        }
        tags = [
            "paper-trail",
            f"env:{settings.app_env}",
            "service:paper_trail",
            f"model_primary:{settings.openrouter_model_primary}",
        ]

        async with span("debate.run", input=trace_input, metadata=trace_metadata):
            update_current_trace(
                name="debate.run",
                input=trace_input,
                tags=tags,
                metadata=trace_metadata,
                session_id=str(debate_id),
            )
            await self.repo.set_status(debate_id, "running")
            state = initial_state(debate.claim, debate.max_rounds)
            graph = graph_mod.build_graph()
            try:
                result = await graph.ainvoke(state)
            except Exception as exc:
                await self.repo.set_status(debate_id, "error")
                update_current_trace(
                    output={"error": f"{type(exc).__name__}: {exc}"},
                    tags=[*tags, "status:error"],
                )
                raise

            verdict = result.get("verdict") or "INCONCLUSIVE"
            confidence = float(result.get("confidence") or 0.0)
            rounds = list(result.get("rounds") or [])
            transcript = str(result.get("transcript_md") or "")

            await self.repo.update_result(
                debate_id,
                verdict=verdict,
                confidence=confidence,
                rounds=rounds,
                transcript_md=transcript,
            )
            update_current_trace(
                output={
                    "verdict": verdict,
                    "confidence": confidence,
                    "rounds_run": len(rounds),
                    "transcript_length": len(transcript),
                },
                tags=[*tags, f"verdict:{verdict}", "status:done"],
                metadata={**trace_metadata, "final_round": result.get("round", 0)},
            )

        return await self.repo.get(debate_id)

    async def get(self, debate_id: UUID) -> Any:
        return await self.repo.get(debate_id)

    async def list(self, cursor: str | None, limit: int = 50) -> tuple[list[Any], str | None]:
        return await self.repo.list_page(cursor, limit)  # type: ignore[no-any-return]
