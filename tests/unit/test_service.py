"""Unit tests for services/debates.py."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

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

    async def update_result(
        self,
        debate_id: UUID,
        verdict: str,
        confidence: float,
        rounds: list[dict],
        transcript_md: str,
    ) -> None:
        d = self.store[debate_id]
        d.verdict = verdict
        d.confidence = confidence
        d.rounds = rounds
        d.transcript_md = transcript_md
        d.status = "done"

    async def set_status(self, debate_id: UUID, status: str) -> None:
        self.store[debate_id].status = status

    async def update_rounds(self, debate_id: UUID, rounds: list[dict]) -> None:
        self.store[debate_id].rounds = list(rounds)

    async def update_judge_progress(
        self,
        debate_id: UUID,
        verdict: str | None,
        confidence: float | None,
    ) -> None:
        d = self.store[debate_id]
        if verdict is not None:
            d.verdict = verdict
        if confidence is not None:
            d.confidence = float(confidence)

    async def list_page(
        self, cursor: str | None, limit: int = 50
    ) -> tuple[list[FakeDebate], str | None]:
        return list(self.store.values()), None


async def test_create_and_run(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo = FakeRepo()
    svc = DebateService(repo)

    class FakeGraph:
        async def astream(self, state, stream_mode="updates"):  # type: ignore[no-untyped-def]
            yield {
                "proponent": {
                    "rounds": [
                        {
                            "side": "proponent",
                            "round": 1,
                            "argument": "a",
                            "evidence": [],
                        }
                    ]
                }
            }
            yield {
                "judge": {
                    "verdict": "TRUE",
                    "confidence": 0.9,
                    "reasoning": "strong evidence",
                    "need_more": False,
                    "round": 1,
                }
            }
            yield {"render": {"transcript_md": "# T"}}

    from paper_trail.agents import graph as graph_mod

    monkeypatch.setattr(graph_mod, "build_graph", lambda: FakeGraph())

    debate_id = await svc.create("the sky is blue", 3)
    assert debate_id in repo.store
    result = await svc.run(debate_id)
    assert result.verdict == "TRUE"
    assert result.confidence == 0.9
    assert result.status == "done"
    assert result.transcript_md == "# T"


async def test_get_and_list() -> None:
    repo = FakeRepo()
    svc = DebateService(repo)
    did = await svc.create("c", 3)
    got = await svc.get(did)
    assert got is not None
    items, cur = await svc.list(None, 50)
    assert len(items) == 1
    assert cur is None


async def test_run_unknown_raises() -> None:
    repo = FakeRepo()
    svc = DebateService(repo)
    with pytest.raises(ValueError):
        await svc.run(uuid4())


async def test_run_graph_error_sets_error_status(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo = FakeRepo()
    svc = DebateService(repo)

    class BrokenGraph:
        async def astream(self, state, stream_mode="updates"):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")
            yield  # pragma: no cover — make this an async generator

    from paper_trail.agents import graph as graph_mod

    monkeypatch.setattr(graph_mod, "build_graph", lambda: BrokenGraph())
    did = await svc.create("c", 3)
    with pytest.raises(RuntimeError):
        await svc.run(did)
    assert repo.store[did].status == "error"
