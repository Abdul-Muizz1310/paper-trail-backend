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


# ---------------------------------------------------------------------------
# Block 6 (Spec 08) — evidence pool + citations
# ---------------------------------------------------------------------------


async def test_proponent_citations_only_contain_pool_cert_ids(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Case 16: invented cert refs never leak into citations.

    The LLM output cites a valid pool cert AND an invented one. Only the
    valid one becomes a citation.
    """
    from uuid import uuid4

    valid = uuid4()
    invented = uuid4()

    async def fake_chat(messages, **kw):  # type: ignore[no-untyped-def]
        return f"See [cert:{valid}] but also [cert:{invented}] which I made up."

    monkeypatch.setattr(mod, "chat", fake_chat)
    pool = [
        {
            "certificate_id": str(valid),
            "url": "https://pool.example",
            "title": "Valid pool item",
            "text": "content",
        }
    ]
    out = await mod.proponent(
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


async def test_proponent_citations_include_urls_when_mentioned(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A URL present in the argument becomes a URL citation."""
    url = "https://example.com/article"

    async def fake_chat(messages, **kw):  # type: ignore[no-untyped-def]
        return f"According to {url} the claim holds."

    monkeypatch.setattr(mod, "chat", fake_chat)
    out = await mod.proponent(
        {
            "claim": "c",
            "max_rounds": 3,
            "round": 0,
            "rounds": [],
            "plan": {
                "sub_questions": [],
                "search_queries": [],
                "evidence": [{"title": "Src", "url": url, "snippet": "s"}],
            },
        }
    )
    citations = out["rounds"][0]["citations"]
    assert any(c["type"] == "url" and c["ref"] == url for c in citations)


async def test_proponent_no_pool_no_cert_citations(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Case 20 partial: without a pool, no cert citations can appear."""
    from uuid import uuid4

    uid = uuid4()

    async def fake_chat(messages, **kw):  # type: ignore[no-untyped-def]
        # Even if the LLM goes rogue, without a pool there's nothing to match.
        return f"[cert:{uid}] I'm hallucinating."

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
    citations = out["rounds"][0]["citations"]
    assert citations == []
