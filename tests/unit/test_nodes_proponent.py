"""Unit tests for agents/nodes/proponent.py."""

from __future__ import annotations

from paper_trail.agents.nodes import proponent as mod


async def test_proponent_with_sub_questions_and_prior_rounds(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Cover proponent.py:32 (sub_questions append) and :38 (prior rounds append)."""

    async def fake_chat(messages, **kw):  # type: ignore[no-untyped-def]
        return "argument with context"

    monkeypatch.setattr(mod, "chat", fake_chat)
    out = await mod.proponent(
        {
            "claim": "c",
            "max_rounds": 3,
            "round": 1,
            "rounds": [
                {"side": "proponent", "round": 1, "argument": "prev-p", "evidence": []},
                {"side": "skeptic", "round": 1, "argument": "prev-s", "evidence": []},
            ],
            "plan": {
                "sub_questions": ["Is the sky blue?", "Why?"],
                "search_queries": [],
                "evidence": [{"title": "T", "url": "https://x", "snippet": "s"}],
            },
        }
    )
    assert len(out["rounds"]) == 1
    assert out["rounds"][0]["round"] == 2


async def test_proponent_returns_single_round(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def fake_chat(messages, **kw):  # type: ignore[no-untyped-def]
        return "the claim is true because reasons"

    monkeypatch.setattr(mod, "chat", fake_chat)
    out = await mod.proponent(
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
    assert r["side"] == "proponent"
    assert r["round"] == 1
    assert "reasons" in r["argument"]
    assert isinstance(r["evidence"], list)
