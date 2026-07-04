"""Service layer — orchestrates the graph and persistence.

Persistence during a run uses a **short-lived session per write** rather than
one long-lived transaction. That does two things the product depends on:

- Each incremental write commits immediately, so SSE consumers polling a
  *separate* session actually see rounds/verdict appear as they are produced
  (the debate feels "live" instead of arriving all at once at the end).
- No pooled connection is pinned for the whole 30-120s debate, so a handful of
  concurrent debates can't exhaust the connection pool (REL-1).

The whole run is bounded by a wall-clock deadline so an upstream rate-limit
storm can't pin a background worker indefinitely (REL-3).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from paper_trail.agents import graph as graph_mod
from paper_trail.agents.state import initial_state
from paper_trail.core.config import settings
from paper_trail.core.langfuse import span, update_current_trace
from paper_trail.models.debate import Debate, DebateStatus
from paper_trail.repositories.debates import DebateRepo

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class DebateService:
    def __init__(
        self,
        repo: DebateRepo | None = None,
        *,
        session_factory: SessionFactory | None = None,
    ) -> None:
        """Bind the service to a persistence strategy.

        - Production (HTTP + background) passes ``session_factory=session_scope``
          and no repo: every operation opens/commits/closes its own short-lived
          session, so nothing is pinned across a long run.
        - Unit tests inject a fake ``repo`` directly (no factory): every
          operation reuses that same in-memory fake.

        Exactly one of the two must be provided.
        """
        if repo is None and session_factory is None:
            raise ValueError("DebateService needs a repo or a session_factory")
        self._repo = repo
        self._session_factory = session_factory

    @asynccontextmanager
    async def _scope(self) -> AsyncIterator[DebateRepo]:
        """Yield a repo for a single unit of work, committing on clean exit.

        With a session factory this opens a fresh short-lived session (and
        ``session_scope`` commits/rolls back on exit). Without one, it yields
        the injected fake repo unchanged.
        """
        if self._session_factory is None:
            assert self._repo is not None
            yield self._repo
            return
        async with self._session_factory() as session:
            yield DebateRepo(session)

    async def create(
        self,
        claim: str,
        max_rounds: int,
        evidence_pool: list[dict[str, Any]] | None = None,
    ) -> UUID:
        async with self._scope() as repo:
            debate = await repo.create(claim, max_rounds, evidence_pool=evidence_pool)
            return debate.id

    async def run(self, debate_id: UUID) -> Debate | None:
        async with self._scope() as repo:
            debate = await repo.get(debate_id)
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
            async with self._scope() as repo:
                await repo.set_status(debate_id, DebateStatus.running)
            # Block 6 (Spec 08): seed the caller-supplied pool into graph
            # state so plan/proponent/skeptic nodes can prefer it over
            # Tavily. Older debates (pool=None) behave identically.
            pool_raw = getattr(debate, "evidence_pool", None)
            pool: list[dict[str, Any]] | None = (
                list(pool_raw) if isinstance(pool_raw, list) and pool_raw else None
            )
            state = initial_state(debate.claim, debate.max_rounds, evidence_pool=pool)
            # OPT-2: the topology is static and depends on no per-request data,
            # so the compiled graph is memoized (see agents.graph.build_graph).
            graph = graph_mod.build_graph()

            # Stream node updates so rounds land in the DB as they're
            # produced, not all at once at the end of ainvoke(). Each write
            # below opens its own short-lived session and commits immediately,
            # so the SSE endpoint (a separate session) sees progress live.
            result: dict[str, Any] = {
                "verdict": None,
                "confidence": None,
                "rounds": [],
                "transcript_md": "",
                "round": 0,
            }
            running_rounds: list[dict[str, Any]] = []
            try:
                # REL-3: bound the whole run so an upstream 429 storm can't pin
                # this worker (and its per-write connections) indefinitely.
                async with asyncio.timeout(settings.debate_deadline_s):
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
                                # Persist + commit so SSE consumers see progress.
                                async with self._scope() as repo:
                                    await repo.update_rounds(debate_id, running_rounds)
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
                                async with self._scope() as repo:
                                    await repo.update_judge_progress(
                                        debate_id,
                                        verdict=result.get("verdict"),
                                        confidence=result.get("confidence"),
                                    )
            except Exception as exc:
                async with self._scope() as repo:
                    await repo.set_status(debate_id, DebateStatus.error)
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
            async with self._scope() as repo:
                await repo.update_result(
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

        async with self._scope() as repo:
            return await repo.get(debate_id)

    async def get(self, debate_id: UUID) -> Debate | None:
        async with self._scope() as repo:
            return await repo.get(debate_id)

    async def list(self, cursor: str | None, limit: int = 50) -> tuple[list[Debate], str | None]:
        async with self._scope() as repo:
            return await repo.list_page(cursor, limit)
