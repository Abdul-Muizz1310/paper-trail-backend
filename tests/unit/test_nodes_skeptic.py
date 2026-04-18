"""Unit tests for agents/nodes/skeptic.py."""

from __future__ import annotations

from paper_trail.agents.nodes import skeptic as mod


async def test_skeptic_with_sub_questions_and_prior_rounds(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Cover skeptic.py:32 (sub_questions) and :38 (prior rounds)."""

    async def fake_chat(messages, **kw):  # type: ignore[no-untyped-def]
        return "rebuttal with context"

    monkeypatch.setattr(mod, "chat", fake_chat)
    out = await mod.skeptic(
        {
            "claim": "c",
            "max_rounds": 3,
            "round": 1,
            "rounds": [
                {"side": "proponent", "round": 1, "argument": "prev-p", "evidence": []},
            ],
            "plan": {
                "sub_questions": ["Is this true?"],
                "search_queries": [],
                "evidence": [{"title": "T", "url": "https://x", "snippet": "s"}],
            },
        }
    )
    assert len(out["rounds"]) == 1
    assert out["rounds"][0]["round"] == 2
    assert out["rounds"][0]["side"] == "skeptic"


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


async def test_skeptic_citations_only_contain_pool_cert_ids(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Case 17: same guarantee as proponent — no invented cert refs."""
    from uuid import uuid4

    valid = uuid4()
    invented = uuid4()

    async def fake_chat(messages, **kw):  # type: ignore[no-untyped-def]
        return f"Counter [cert:{valid}] and [cert:{invented}] is made up."

    monkeypatch.setattr(mod, "chat", fake_chat)
    pool = [
        {
            "certificate_id": str(valid),
            "url": "https://pool.example",
            "title": "Valid",
            "text": "content",
        }
    ]
    out = await mod.skeptic(
        {
            "claim": "c",
            "max_rounds": 3,
            "round": 0,
            "rounds": [],
            "plan": {"sub_questions": [], "search_queries": [], "evidence": []},
            "evidence_pool": pool,
        }
    )
    citations = out["rounds"][0]["citations"]
    refs = [c["ref"] for c in citations]
    assert str(valid) in refs
    assert str(invented) not in refs
