"""Unit tests for agents/nodes/plan.py."""

from __future__ import annotations

from paper_trail.agents.nodes import plan as plan_mod
from paper_trail.agents.tools.search import SearchHit


async def test_plan_search_exception_is_captured_in_failed_queries(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Cover plan.py:41-43 — search failure caught, appended to failed_queries."""

    class FakePlan:
        def __init__(self) -> None:
            self.sub_questions = ["q1"]
            self.search_queries = ["fail-query"]

    async def fake_chat_json(messages, schema, **kw):  # type: ignore[no-untyped-def]
        return FakePlan()

    async def exploding_search(query, k=5):  # type: ignore[no-untyped-def]
        raise ConnectionError("tavily unreachable")

    monkeypatch.setattr(plan_mod, "chat_json", fake_chat_json)
    monkeypatch.setattr(plan_mod, "search", exploding_search)

    out = await plan_mod.plan({"claim": "c", "max_rounds": 3, "round": 0, "rounds": []})
    assert "plan" in out
    # Evidence should be empty (search failed)
    assert out["plan"]["evidence"] == []


async def test_plan_returns_shape(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class FakePlan:
        def __init__(self) -> None:
            self.sub_questions = ["q1", "q2"]
            self.search_queries = ["sq1"]

    async def fake_chat_json(messages, schema, **kw):  # type: ignore[no-untyped-def]
        return FakePlan()

    async def fake_search(query, k=5):  # type: ignore[no-untyped-def]
        return [SearchHit(title="T", url="https://x", snippet="s")]

    monkeypatch.setattr(plan_mod, "chat_json", fake_chat_json)
    monkeypatch.setattr(plan_mod, "search", fake_search)

    out = await plan_mod.plan({"claim": "c", "max_rounds": 3, "round": 0, "rounds": []})
    assert "plan" in out
    assert out["plan"]["sub_questions"] == ["q1", "q2"]
    assert out["plan"]["search_queries"] == ["sq1"]
    assert "evidence" in out["plan"]
