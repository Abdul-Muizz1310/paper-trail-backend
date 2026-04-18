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
        self.evidence_pool: list[dict] | None = None
        self.rounds_struct: list[dict] | None = None
        self.transcript_hash: str | None = None


class FakeRepo:
    def __init__(self) -> None:
        self.store: dict[UUID, FakeDebate] = {}

    async def create(
        self,
        claim: str,
        max_rounds: int,
        evidence_pool: list[dict] | None = None,
    ) -> FakeDebate:
        d = FakeDebate(uuid4(), claim, max_rounds)
        d.evidence_pool = evidence_pool if evidence_pool else None
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
        rounds_struct: list[dict] | None = None,
        transcript_hash: str | None = None,
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


async def test_run_skips_non_dict_graph_updates(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Cover debates.py:81 — non-dict update values are silently skipped."""
    repo = FakeRepo()
    svc = DebateService(repo)

    class MixedGraph:
        async def astream(self, state, stream_mode="updates"):  # type: ignore[no-untyped-def]
            # Some LangGraph internals emit string or None updates
            yield {"__start__": "not-a-dict"}
            yield {
                "proponent": {
                    "rounds": [{"side": "proponent", "round": 1, "argument": "a", "evidence": []}]
                }
            }
            yield {"judge": {"verdict": "TRUE", "confidence": 0.9, "need_more": False, "round": 1}}
            yield {"render": {"transcript_md": "# T"}}

    from paper_trail.agents import graph as graph_mod

    monkeypatch.setattr(graph_mod, "build_graph", lambda: MixedGraph())
    did = await svc.create("sky is blue", 3)
    result = await svc.run(did)
    assert result.verdict == "TRUE"
    assert result.status == "done"


# ---------------------------------------------------------------------------
# Block 6 (Spec 08)
# ---------------------------------------------------------------------------


async def test_create_with_evidence_pool_passes_through() -> None:
    repo = FakeRepo()
    svc = DebateService(repo)
    pool = [
        {
            "certificate_id": str(uuid4()),
            "url": "https://x",
            "title": "t",
            "text": "body",
        }
    ]
    did = await svc.create("claim", 3, evidence_pool=pool)
    assert repo.store[did].evidence_pool == pool


async def test_run_persists_rounds_struct_and_hash(monkeypatch) -> None:  # type: ignore[no-untyped-def]
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
                            "citations": [{"type": "url", "ref": "https://u", "title": "T"}],
                        }
                    ]
                }
            }
            yield {
                "judge": {
                    "verdict": "TRUE",
                    "confidence": 0.9,
                    "need_more": False,
                    "round": 1,
                }
            }
            yield {
                "render": {
                    "transcript_md": "# T",
                    "rounds_struct": [
                        {
                            "side": "proponent",
                            "round": 1,
                            "argument_md": "a",
                            "citations": [{"type": "url", "ref": "https://u", "title": "T"}],
                        }
                    ],
                    "transcript_hash": "b" * 64,
                }
            }

    from paper_trail.agents import graph as graph_mod

    monkeypatch.setattr(graph_mod, "build_graph", lambda: FakeGraph())
    did = await svc.create("claim", 3)
    result = await svc.run(did)
    assert result is not None
    assert result.rounds_struct is not None
    assert len(result.rounds_struct) == 1
    assert result.transcript_hash == "b" * 64


async def test_run_seeds_pool_into_initial_state(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The service must pass the persisted pool into `initial_state`."""
    repo = FakeRepo()
    svc = DebateService(repo)
    pool = [
        {
            "certificate_id": str(uuid4()),
            "url": "https://x",
            "title": "t",
            "text": "body",
        }
    ]
    did = await svc.create("claim", 3, evidence_pool=pool)

    captured_state: dict = {}

    class PoolAwareGraph:
        async def astream(self, state, stream_mode="updates"):  # type: ignore[no-untyped-def]
            captured_state["seen"] = state
            yield {"render": {"transcript_md": "# T"}}

    from paper_trail.agents import graph as graph_mod

    monkeypatch.setattr(graph_mod, "build_graph", lambda: PoolAwareGraph())
    await svc.run(did)
    assert captured_state["seen"]["evidence_pool"] == pool


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
