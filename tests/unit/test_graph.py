"""Unit tests for agents/graph.py."""

from __future__ import annotations

from paper_trail.agents import graph as graph_mod
from paper_trail.agents.state import initial_state


def _patch_nodes(monkeypatch, *, judge_confidences: list[float]) -> None:  # type: ignore[no-untyped-def]
    from paper_trail.agents.nodes import judge as j
    from paper_trail.agents.nodes import plan as p
    from paper_trail.agents.nodes import proponent as pr
    from paper_trail.agents.nodes import render as rd
    from paper_trail.agents.nodes import skeptic as sk

    async def fake_plan(state):  # type: ignore[no-untyped-def]
        return {"plan": {"sub_questions": ["q"], "search_queries": ["sq"], "evidence": []}}

    async def fake_pro(state):  # type: ignore[no-untyped-def]
        r = state.get("round", 0) + 1
        return {"rounds": [{"side": "proponent", "round": r, "argument": "pro", "evidence": []}]}

    async def fake_sk(state):  # type: ignore[no-untyped-def]
        r = state.get("round", 0) + 1
        return {"rounds": [{"side": "skeptic", "round": r, "argument": "con", "evidence": []}]}

    calls = {"i": 0}

    async def fake_judge(state):  # type: ignore[no-untyped-def]
        c = judge_confidences[min(calls["i"], len(judge_confidences) - 1)]
        calls["i"] += 1
        return {
            "verdict": "TRUE",
            "confidence": c,
            "need_more": c < 0.85,
            "round": state.get("round", 0) + 1,
        }

    async def fake_render(state):  # type: ignore[no-untyped-def]
        return {"transcript_md": f"# {state['claim']}\n{len(state['rounds'])} rounds"}

    monkeypatch.setattr(p, "plan", fake_plan)
    monkeypatch.setattr(pr, "proponent", fake_pro)
    monkeypatch.setattr(sk, "skeptic", fake_sk)
    monkeypatch.setattr(j, "judge", fake_judge)
    monkeypatch.setattr(rd, "render", fake_render)


def test_build_graph_compiles() -> None:
    g = graph_mod.build_graph()
    assert g is not None


async def test_graph_converges_round_one(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_nodes(monkeypatch, judge_confidences=[0.9])
    g = graph_mod.build_graph()
    result = await g.ainvoke(initial_state("the sky is blue", max_rounds=5))
    assert result["verdict"] == "TRUE"
    assert result["transcript_md"].startswith("#")
    assert len(result["rounds"]) == 2


async def test_graph_cycles_until_converge(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_nodes(monkeypatch, judge_confidences=[0.3, 0.9])
    g = graph_mod.build_graph()
    result = await g.ainvoke(initial_state("claim", max_rounds=5))
    assert len(result["rounds"]) == 4  # 2 rounds x (pro+con)
    assert result["verdict"] == "TRUE"


async def test_graph_stops_at_max_rounds(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_nodes(monkeypatch, judge_confidences=[0.1, 0.1, 0.1, 0.1, 0.1])
    g = graph_mod.build_graph()
    result = await g.ainvoke(initial_state("claim", max_rounds=2))
    assert len(result["rounds"]) == 4
    assert "transcript_md" in result


# ---------------------------------------------------------------------------
# Block 6 (Spec 08) — integration: evidence pool flows through the graph
# ---------------------------------------------------------------------------


def _patch_nodes_with_citations(monkeypatch, cite_cert_id: str | None) -> None:  # type: ignore[no-untyped-def]
    """Patch every node to deterministic fakes that surface `cite_cert_id`.

    When `cite_cert_id` is set, proponent/skeptic emit a `[cert:<uuid>]`
    marker; otherwise they produce plain text. This lets us drive an
    end-to-end run that exercises the real render node (with its
    rounds_struct + hash logic).
    """
    from paper_trail.agents.nodes import judge as j
    from paper_trail.agents.nodes import plan as p
    from paper_trail.agents.nodes import proponent as pr
    from paper_trail.agents.nodes import skeptic as sk

    async def fake_plan(state):  # type: ignore[no-untyped-def]
        return {"plan": {"sub_questions": ["q"], "search_queries": [], "evidence": []}}

    async def fake_chat(messages, **kw):  # type: ignore[no-untyped-def]
        if cite_cert_id:
            return f"Strong evidence at [cert:{cite_cert_id}]."
        return "Plain argument with no cert markers."

    class _FakeVerdict:
        verdict = "TRUE"
        confidence = 0.9
        reasoning = ""

    async def fake_chat_json(messages, schema, **kw):  # type: ignore[no-untyped-def]
        return _FakeVerdict()

    monkeypatch.setattr(p, "plan", fake_plan)
    # Hook the real proponent/skeptic nodes (so citations resolution runs)
    # but short-circuit their LLM call.
    monkeypatch.setattr(pr, "chat", fake_chat)
    monkeypatch.setattr(sk, "chat", fake_chat)
    monkeypatch.setattr(j, "chat_json", fake_chat_json)


async def test_graph_end_to_end_with_pool_has_cert_citations(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Case 19."""
    from uuid import uuid4

    cid = uuid4()
    _patch_nodes_with_citations(monkeypatch, cite_cert_id=str(cid))
    g = graph_mod.build_graph()
    pool = [
        {
            "certificate_id": str(cid),
            "url": "https://pool.example",
            "title": "Pool",
            "text": "content",
        }
    ]
    state = initial_state("claim", max_rounds=1, evidence_pool=pool)
    result = await g.ainvoke(state)
    # rounds_struct is produced by render and carries resolved citations
    rs = result["rounds_struct"]
    all_citations = [c for r in rs for c in r["citations"]]
    assert any(c["type"] == "cert" and c["ref"] == str(cid) for c in all_citations)


async def test_graph_end_to_end_without_pool_has_no_cert_citations(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Case 20."""
    _patch_nodes_with_citations(monkeypatch, cite_cert_id=None)
    g = graph_mod.build_graph()
    state = initial_state("claim", max_rounds=1)
    result = await g.ainvoke(state)
    rs = result["rounds_struct"]
    all_citations = [c for r in rs for c in r["citations"]]
    assert all(c["type"] != "cert" for c in all_citations)
    # Also verify the markdown transcript doesn't contain any fake cert markers
    assert "[cert:" not in result["transcript_md"]


async def test_graph_transcript_hash_stable_across_runs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Case 21 at the graph layer."""
    from uuid import uuid4

    cid = uuid4()
    _patch_nodes_with_citations(monkeypatch, cite_cert_id=str(cid))
    g = graph_mod.build_graph()
    pool = [
        {
            "certificate_id": str(cid),
            "url": "https://p",
            "title": "P",
            "text": "t",
        }
    ]
    state1 = initial_state("claim", max_rounds=1, evidence_pool=pool)
    state2 = initial_state("claim", max_rounds=1, evidence_pool=pool)
    r1 = await g.ainvoke(state1)
    r2 = await g.ainvoke(state2)
    assert r1["transcript_hash"] == r2["transcript_hash"]
