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
    assert len(result["rounds"]) == 4  # 2 rounds × (pro+con)
    assert result["verdict"] == "TRUE"


async def test_graph_stops_at_max_rounds(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_nodes(monkeypatch, judge_confidences=[0.1, 0.1, 0.1, 0.1, 0.1])
    g = graph_mod.build_graph()
    result = await g.ainvoke(initial_state("claim", max_rounds=2))
    assert len(result["rounds"]) == 4
    assert "transcript_md" in result
