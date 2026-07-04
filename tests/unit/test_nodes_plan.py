"""Unit tests for agents/nodes/plan.py."""

from __future__ import annotations

from paper_trail.agents.nodes import plan as plan_mod
from paper_trail.agents.tools.fetch import FetchedDoc
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

    async def fake_fetch(url):  # type: ignore[no-untyped-def]
        return FetchedDoc(url=url, text="full article body")

    monkeypatch.setattr(plan_mod, "chat_json", fake_chat_json)
    monkeypatch.setattr(plan_mod, "search", fake_search)
    monkeypatch.setattr(plan_mod, "fetch", fake_fetch)

    out = await plan_mod.plan({"claim": "c", "max_rounds": 3, "round": 0, "rounds": []})
    assert "plan" in out
    assert out["plan"]["sub_questions"] == ["q1", "q2"]
    assert out["plan"]["search_queries"] == ["sq1"]
    assert "evidence" in out["plan"]


async def test_plan_fetches_full_article_text_for_hits(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """FIX (dead-code): plan wires fetch() to attach extracted body text."""

    class FakePlan:
        def __init__(self) -> None:
            self.sub_questions = ["q1"]
            self.search_queries = ["sq1"]

    async def fake_chat_json(messages, schema, **kw):  # type: ignore[no-untyped-def]
        return FakePlan()

    async def fake_search(query, k=5):  # type: ignore[no-untyped-def]
        return [SearchHit(title="T", url="https://x", snippet="short snippet")]

    fetched_urls: list[str] = []

    async def fake_fetch(url):  # type: ignore[no-untyped-def]
        fetched_urls.append(url)
        return FetchedDoc(url=url, text="the full extracted article body text")

    monkeypatch.setattr(plan_mod, "chat_json", fake_chat_json)
    monkeypatch.setattr(plan_mod, "search", fake_search)
    monkeypatch.setattr(plan_mod, "fetch", fake_fetch)

    out = await plan_mod.plan({"claim": "c", "max_rounds": 3, "round": 0, "rounds": []})
    ev = out["plan"]["evidence"]
    assert ev[0]["text"] == "the full extracted article body text"
    assert fetched_urls == ["https://x"]


async def test_plan_runs_searches_concurrently(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """OPT-1: independent Tavily queries are dispatched concurrently."""
    import asyncio

    class FakePlan:
        def __init__(self) -> None:
            self.sub_questions = ["q1"]
            self.search_queries = ["a", "b", "c"]

    async def fake_chat_json(messages, schema, **kw):  # type: ignore[no-untyped-def]
        return FakePlan()

    in_flight = 0
    max_in_flight = 0

    async def fake_search(query, k=5):  # type: ignore[no-untyped-def]
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return []

    monkeypatch.setattr(plan_mod, "chat_json", fake_chat_json)
    monkeypatch.setattr(plan_mod, "search", fake_search)

    await plan_mod.plan({"claim": "c", "max_rounds": 3, "round": 0, "rounds": []})
    # If searches were sequential, max_in_flight would be 1.
    assert max_in_flight == 3


async def test_plan_fetch_failure_keeps_snippet(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A failed article fetch must not drop the snippet-only evidence entry."""
    from paper_trail.core.errors import ToolError

    class FakePlan:
        def __init__(self) -> None:
            self.sub_questions = ["q1"]
            self.search_queries = ["sq1"]

    async def fake_chat_json(messages, schema, **kw):  # type: ignore[no-untyped-def]
        return FakePlan()

    async def fake_search(query, k=5):  # type: ignore[no-untyped-def]
        return [SearchHit(title="T", url="https://x", snippet="kept snippet")]

    async def exploding_fetch(url):  # type: ignore[no-untyped-def]
        raise ToolError("fetch_bad_status", "status=500")

    monkeypatch.setattr(plan_mod, "chat_json", fake_chat_json)
    monkeypatch.setattr(plan_mod, "search", fake_search)
    monkeypatch.setattr(plan_mod, "fetch", exploding_fetch)

    out = await plan_mod.plan({"claim": "c", "max_rounds": 3, "round": 0, "rounds": []})
    ev = out["plan"]["evidence"]
    assert ev[0]["snippet"] == "kept snippet"
    assert "text" not in ev[0]


# ---------------------------------------------------------------------------
# Block 6 (Spec 08) — evidence pool support
# ---------------------------------------------------------------------------


async def test_plan_prompt_includes_evidence_pool_when_present(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Case 14: plan user message mentions pool when state has one."""
    from uuid import uuid4

    captured_messages: list[list[dict[str, str]]] = []

    class FakePlan:
        def __init__(self) -> None:
            self.sub_questions = ["q1"]
            self.search_queries = []

    async def fake_chat_json(messages, schema, **kw):  # type: ignore[no-untyped-def]
        captured_messages.append(messages)
        return FakePlan()

    async def fake_search(query, k=5):  # type: ignore[no-untyped-def]
        return []

    monkeypatch.setattr(plan_mod, "chat_json", fake_chat_json)
    monkeypatch.setattr(plan_mod, "search", fake_search)

    cid = uuid4()
    pool = [
        {
            "certificate_id": str(cid),
            "url": "https://example.com",
            "title": "Pool item 1",
            "text": "authoritative content",
        }
    ]
    await plan_mod.plan(
        {
            "claim": "c",
            "max_rounds": 3,
            "round": 0,
            "rounds": [],
            "evidence_pool": pool,
        }
    )
    user_msg = captured_messages[0][-1]["content"]
    # Prompt must reference the pool (title + cert marker syntax).
    assert "Pool item 1" in user_msg
    assert f"[cert:{cid}]" in user_msg


async def test_plan_prompt_omits_pool_when_absent(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Case 15: plan user message matches original wording when no pool."""
    captured_messages: list[list[dict[str, str]]] = []

    class FakePlan:
        def __init__(self) -> None:
            self.sub_questions = ["q1"]
            self.search_queries = []

    async def fake_chat_json(messages, schema, **kw):  # type: ignore[no-untyped-def]
        captured_messages.append(messages)
        return FakePlan()

    async def fake_search(query, k=5):  # type: ignore[no-untyped-def]
        return []

    monkeypatch.setattr(plan_mod, "chat_json", fake_chat_json)
    monkeypatch.setattr(plan_mod, "search", fake_search)

    await plan_mod.plan({"claim": "c", "max_rounds": 3, "round": 0, "rounds": []})
    user_msg = captured_messages[0][-1]["content"]
    assert user_msg == "Decompose this claim: c"
    assert "cert:" not in user_msg


async def test_plan_empty_pool_treated_as_absent(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """An explicit empty-list pool must not trigger the pool-aware prompt."""
    captured_messages: list[list[dict[str, str]]] = []

    class FakePlan:
        def __init__(self) -> None:
            self.sub_questions = ["q1"]
            self.search_queries = []

    async def fake_chat_json(messages, schema, **kw):  # type: ignore[no-untyped-def]
        captured_messages.append(messages)
        return FakePlan()

    async def fake_search(query, k=5):  # type: ignore[no-untyped-def]
        return []

    monkeypatch.setattr(plan_mod, "chat_json", fake_chat_json)
    monkeypatch.setattr(plan_mod, "search", fake_search)

    await plan_mod.plan(
        {"claim": "c", "max_rounds": 3, "round": 0, "rounds": [], "evidence_pool": []}
    )
    user_msg = captured_messages[0][-1]["content"]
    assert user_msg == "Decompose this claim: c"
