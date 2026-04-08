"""Unit tests for agents/nodes/skeptic.py."""

from __future__ import annotations

from paper_trail.agents.nodes import skeptic as mod


async def test_skeptic_returns_single_round(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def fake_chat(messages, **kw):  # type: ignore[no-untyped-def]
        return "the claim is false because"

    monkeypatch.setattr(mod, "chat", fake_chat)
    out = await mod.skeptic(
        {
            "claim": "c",
            "max_rounds": 3,
            "round": 0,
            "rounds": [],
            "plan": {"sub_questions": [], "search_queries": [], "evidence": []},
        }
    )
    assert "rounds" in out
    assert len(out["rounds"]) == 1
    r = out["rounds"][0]
    assert r["side"] == "skeptic"
    assert r["round"] == 1
    assert "false" in r["argument"]
