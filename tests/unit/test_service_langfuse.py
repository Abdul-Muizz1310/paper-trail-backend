"""Smoke test: langfuse decorator on DebateService.run is transparent."""

from __future__ import annotations

from uuid import UUID, uuid4

from paper_trail.services.debates import DebateService


class FakeDebate:
    def __init__(self, id: UUID, claim: str, max_rounds: int) -> None:
        self.id = id
        self.claim = claim
        self.max_rounds = max_rounds
        self.status = "pending"
        self.verdict: str | None = None
        self.confidence: float | None = None
        self.rounds: list[dict] = []
        self.transcript_md: str | None = None
        self.evidence_pool: list[dict] | None = None
        self.rounds_struct: list[dict] | None = None
        self.transcript_hash: str | None = None


class FakeRepo:
    def __init__(self) -> None:
        self.store: dict[UUID, FakeDebate] = {}

    async def create(  # type: ignore[no-untyped-def]
        self, claim: str, max_rounds: int, evidence_pool=None
    ) -> FakeDebate:
        d = FakeDebate(uuid4(), claim, max_rounds)
        d.evidence_pool = evidence_pool if evidence_pool else None
        self.store[d.id] = d
        return d

    async def get(self, debate_id: UUID) -> FakeDebate | None:
        return self.store.get(debate_id)

    async def update_result(  # type: ignore[no-untyped-def]
        self,
        debate_id,
        verdict,
        confidence,
        rounds,
        transcript_md,
        rounds_struct=None,
        transcript_hash=None,
    ) -> None:
        d = self.store[debate_id]
        d.verdict = verdict
        d.confidence = confidence
        d.rounds = rounds
        d.transcript_md = transcript_md
        d.rounds_struct = rounds_struct
        d.transcript_hash = transcript_hash
        d.status = "done"

    async def set_status(self, debate_id: UUID, status: str) -> None:
        self.store[debate_id].status = status

    async def update_rounds(self, debate_id: UUID, rounds: list[dict]) -> None:
        self.store[debate_id].rounds = list(rounds)

    async def update_judge_progress(  # type: ignore[no-untyped-def]
        self, debate_id, verdict, confidence
    ) -> None:
        d = self.store[debate_id]
        if verdict is not None:
            d.verdict = verdict
        if confidence is not None:
            d.confidence = float(confidence)


async def test_run_returns_value_under_noop_langfuse(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The @trace decorator must preserve the wrapped function's return value
    even when langfuse is in no-op (unconfigured) mode."""
    from paper_trail.core import langfuse as lf

    # force no-op
    monkeypatch.setattr(lf, "_client", None)
    monkeypatch.setattr(lf, "_client_initialized", True)

    repo = FakeRepo()
    svc = DebateService(repo)

    class FakeGraph:
        async def astream(self, state, stream_mode="updates"):  # type: ignore[no-untyped-def]
            yield {
                "judge": {
                    "verdict": "FALSE",
                    "confidence": 0.42,
                    "reasoning": "r",
                    "need_more": False,
                    "round": 1,
                }
            }
            yield {"render": {"transcript_md": "# x"}}

    from paper_trail.agents import graph as graph_mod

    monkeypatch.setattr(graph_mod, "build_graph", lambda: FakeGraph())

    did = await svc.create("c", 2)
    result = await svc.run(did)
    assert result is not None
    assert result.verdict == "FALSE"
    assert result.confidence == 0.42
