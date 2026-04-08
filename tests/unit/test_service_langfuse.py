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


class FakeRepo:
    def __init__(self) -> None:
        self.store: dict[UUID, FakeDebate] = {}

    async def create(self, claim: str, max_rounds: int) -> FakeDebate:
        d = FakeDebate(uuid4(), claim, max_rounds)
        self.store[d.id] = d
        return d

    async def get(self, debate_id: UUID) -> FakeDebate | None:
        return self.store.get(debate_id)

    async def update_result(self, debate_id, verdict, confidence, rounds, transcript_md) -> None:  # type: ignore[no-untyped-def]
        d = self.store[debate_id]
        d.verdict = verdict
        d.confidence = confidence
        d.rounds = rounds
        d.transcript_md = transcript_md
        d.status = "done"

    async def set_status(self, debate_id: UUID, status: str) -> None:
        self.store[debate_id].status = status


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
        async def ainvoke(self, state):  # type: ignore[no-untyped-def]
            return {
                **state,
                "verdict": "FALSE",
                "confidence": 0.42,
                "rounds": [],
                "transcript_md": "# x",
            }

    from paper_trail.agents import graph as graph_mod

    monkeypatch.setattr(graph_mod, "build_graph", lambda: FakeGraph())

    did = await svc.create("c", 2)
    result = await svc.run(did)
    assert result is not None
    assert result.verdict == "FALSE"
    assert result.confidence == 0.42
