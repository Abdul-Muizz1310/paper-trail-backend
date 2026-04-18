"""Service layer — orchestrates the graph and persistence."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from paper_trail.agents import graph as graph_mod
from paper_trail.agents.state import initial_state
from paper_trail.core.config import settings
from paper_trail.core.langfuse import span, update_current_trace
from paper_trail.models.debate import Debate, DebateStatus
from paper_trail.repositories.debates import DebateRepo


class DebateService:
    def __init__(self, repo: DebateRepo) -> None:
        self.repo = repo

    async def create(
        self,
        claim: str,
        max_rounds: int,
        evidence_pool: list[dict[str, Any]] | None = None,
    ) -> UUID:
        debate = await self.repo.create(claim, max_rounds, evidence_pool=evidence_pool)
        return debate.id

    async def run(self, debate_id: UUID) -> Debate | None:
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
            await self.repo.set_status(debate_id, DebateStatus.running)
            # Block 6 (Spec 08): seed the caller-supplied pool into graph
            # state so plan/proponent/skeptic nodes can prefer it over
            # Tavily. Older debates (pool=None) behave identically.
            pool_raw = getattr(debate, "evidence_pool", None)
            pool: list[dict[str, Any]] | None = (
                list(pool_raw) if isinstance(pool_raw, list) and pool_raw else None
            )
            state = initial_state(debate.claim, debate.max_rounds, evidence_pool=pool)
            graph = graph_mod.build_graph()

            # Stream node updates so rounds land in the DB as they're
            # produced, not all at once at the end of ainvoke(). The
            # SSE endpoint polls repo.get(), so incremental persistence
            # is what actually makes the debate feel "live".
            result: dict[str, Any] = {
                "verdict": None,
                "confidence": None,
                "rounds": [],
                "transcript_md": "",
                "round": 0,
            }
            running_rounds: list[dict[str, Any]] = []
            try:
                async for chunk in graph.astream(state, stream_mode="updates"):
                    # `chunk` is {node_name: node_output_dict}. proponent
                    # and skeptic return {"rounds": [entry]} which the
                    # state reducer would concat; we mirror that here so
                    # we can persist after each node.
                    for node_name, update in chunk.items():
                        if not isinstance(update, dict):
                            continue
                        # Rounds: append (reducer is operator.add).
                        new_rounds = update.get("rounds")
                        if isinstance(new_rounds, list) and new_rounds:
                            running_rounds = [*running_rounds, *new_rounds]
                            result["rounds"] = running_rounds
                            # Persist so SSE consumers see progress.
                            await self.repo.update_rounds(debate_id, running_rounds)
                        # Judge / render emit top-level fields.
                        for key in (
                            "verdict",
                            "confidence",
                            "transcript_md",
                            "round",
                            "need_more",
                            "plan",
                            # Block 6: render now also emits these.
                            "rounds_struct",
                            "transcript_hash",
                        ):
                            if key in update:
                                result[key] = update[key]
                        # Mirror LangGraph's reducer: after judge,
                        # persist the verdict + confidence (so the
                        # confidence bar fills live in the UI).
                        if node_name == "judge":
                            await self.repo.update_judge_progress(
                                debate_id,
                                verdict=result.get("verdict"),
                                confidence=result.get("confidence"),
                            )
            except Exception as exc:
                await self.repo.set_status(debate_id, DebateStatus.error)
                update_current_trace(
                    output={"error": f"{type(exc).__name__}: {exc}"},
                    tags=[*tags, "status:error"],
                )
                raise

            verdict = result.get("verdict") or "INCONCLUSIVE"
            confidence = float(result.get("confidence") or 0.0)
            rounds = list(result.get("rounds") or [])
            transcript = str(result.get("transcript_md") or "")

            rounds_struct_raw = result.get("rounds_struct")
            rounds_struct: list[dict[str, Any]] | None = (
                list(rounds_struct_raw) if isinstance(rounds_struct_raw, list) else None
            )
            transcript_hash_raw = result.get("transcript_hash")
            transcript_hash: str | None = (
                str(transcript_hash_raw) if isinstance(transcript_hash_raw, str) else None
            )
            await self.repo.update_result(
                debate_id,
                verdict=verdict,
                confidence=confidence,
                rounds=rounds,
                transcript_md=transcript,
                rounds_struct=rounds_struct,
                transcript_hash=transcript_hash,
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

    async def get(self, debate_id: UUID) -> Debate | None:
        return await self.repo.get(debate_id)

    async def list(self, cursor: str | None, limit: int = 50) -> tuple[list[Debate], str | None]:
        return await self.repo.list_page(cursor, limit)
