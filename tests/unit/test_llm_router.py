"""Unit tests for core/llm.py (OpenRouter router with fallback)."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import pytest
import respx
from pydantic import BaseModel

from paper_trail.core import llm as llm_mod
from paper_trail.core.config import settings
from paper_trail.core.errors import LLMError


def _chat_url() -> str:
    return f"{settings.openrouter_base_url}/chat/completions"


def _resp(content: str) -> dict:
    return {
        "id": "x",
        "choices": [{"message": {"role": "assistant", "content": content}}],
    }


@respx.mock
async def test_chat_happy_path() -> None:
    route = respx.post(_chat_url()).mock(return_value=httpx.Response(200, json=_resp("hi")))
    out = await llm_mod.chat([{"role": "user", "content": "hello"}])
    assert out == "hi"
    assert route.called
    req = route.calls.last.request
    assert req.headers.get("HTTP-Referer") == settings.openrouter_http_referer
    assert req.headers.get("X-Title") == settings.openrouter_x_title


@respx.mock
async def test_chat_primary_429_falls_back() -> None:
    calls = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["i"] += 1
        if calls["i"] == 1:
            return httpx.Response(429, json={"error": "rate"})
        return httpx.Response(200, json=_resp("fallback-content"))

    respx.post(_chat_url()).mock(side_effect=handler)
    out = await llm_mod.chat([{"role": "user", "content": "hi"}])
    assert out == "fallback-content"
    assert calls["i"] == 2


@respx.mock
async def test_chat_primary_500_falls_back() -> None:
    calls = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["i"] += 1
        if calls["i"] == 1:
            return httpx.Response(500)
        return httpx.Response(200, json=_resp("ok"))

    respx.post(_chat_url()).mock(side_effect=handler)
    out = await llm_mod.chat([{"role": "user", "content": "hi"}])
    assert out == "ok"


@respx.mock
async def test_chat_all_fail_raises() -> None:
    respx.post(_chat_url()).mock(return_value=httpx.Response(500))
    with pytest.raises(LLMError):
        await llm_mod.chat([{"role": "user", "content": "hi"}])


class _Schema(BaseModel):
    answer: str
    score: int


@respx.mock
async def test_chat_json_happy_path_validates() -> None:
    route = respx.post(_chat_url()).mock(
        return_value=httpx.Response(200, json=_resp('{"answer": "yes", "score": 7}'))
    )
    result = await llm_mod.chat_json([{"role": "user", "content": "q"}], _Schema)
    assert isinstance(result, _Schema)
    assert result.answer == "yes"
    body = route.calls.last.request.content.decode()
    assert "response_format" in body
    assert "json_object" in body


@respx.mock
async def test_chat_json_invalid_json_raises() -> None:
    respx.post(_chat_url()).mock(return_value=httpx.Response(200, json=_resp("not json at all")))
    with pytest.raises(LLMError):
        await llm_mod.chat_json([{"role": "user", "content": "q"}], _Schema)


@respx.mock
async def test_chat_json_schema_mismatch_raises() -> None:
    respx.post(_chat_url()).mock(return_value=httpx.Response(200, json=_resp('{"wrong": "field"}')))
    with pytest.raises(LLMError):
        await llm_mod.chat_json([{"role": "user", "content": "q"}], _Schema)


# ---------------------------------------------------------------------------
# Error paths — timeout, http error, malformed response, 400, retry exhaust
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Skip backoff sleeps so retry loops finish instantly; record what would have been slept."""
    slept: list[float] = []

    async def _noop(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(llm_mod, "_backoff_sleep", _noop)
    return slept


@pytest.fixture(autouse=True)
def _distinct_tiers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three different slugs, so a test can tell the tiers apart by the model it was sent."""
    monkeypatch.setattr(settings, "openrouter_model_primary", "p/primary:free")
    monkeypatch.setattr(settings, "openrouter_model_fast", "f/fast:free")
    monkeypatch.setattr(settings, "openrouter_model_fallback", "b/fallback:free")
    monkeypatch.setattr(settings, "llm_node_deadline_s", 120.0)


@respx.mock
async def test_chat_timeout_on_primary_falls_back_to_fallback() -> None:
    calls = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["i"] += 1
        if calls["i"] == 1:
            raise httpx.TimeoutException("timed out")
        return httpx.Response(200, json=_resp("rescued"))

    respx.post(_chat_url()).mock(side_effect=handler)
    out = await llm_mod.chat([{"role": "user", "content": "hi"}])
    assert out == "rescued"


@respx.mock
async def test_chat_timeout_on_both_raises_all_models_exhausted() -> None:
    respx.post(_chat_url()).mock(side_effect=httpx.TimeoutException("timed out"))
    with pytest.raises(LLMError) as exc_info:
        await llm_mod.chat([{"role": "user", "content": "hi"}])
    assert exc_info.value.stage == "all_models_exhausted"


@respx.mock
async def test_chat_http_error_falls_back() -> None:
    calls = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["i"] += 1
        if calls["i"] == 1:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json=_resp("fallback-rescued"))

    respx.post(_chat_url()).mock(side_effect=handler)
    out = await llm_mod.chat([{"role": "user", "content": "hi"}])
    assert out == "fallback-rescued"


@respx.mock
async def test_chat_400_client_error_falls_back() -> None:
    calls = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["i"] += 1
        if calls["i"] == 1:
            return httpx.Response(400, json={"error": "bad request"})
        return httpx.Response(200, json=_resp("ok"))

    respx.post(_chat_url()).mock(side_effect=handler)
    out = await llm_mod.chat([{"role": "user", "content": "hi"}])
    assert out == "ok"


@respx.mock
async def test_chat_bad_response_shape_raises() -> None:
    """Well-formed 200 but missing the expected choices/message/content path."""
    respx.post(_chat_url()).mock(return_value=httpx.Response(200, json={"nope": True}))
    with pytest.raises(LLMError) as exc_info:
        await llm_mod.chat([{"role": "user", "content": "hi"}])
    # Both primary + fallback raise bad_response → exhausted
    assert exc_info.value.stage == "all_models_exhausted"


@respx.mock
async def test_chat_rate_limit_exhausts_retries(_no_sleep) -> None:  # type: ignore[no-untyped-def]
    """All 5 retries return 429 → raises rate_limited from the retry loop."""
    respx.post(_chat_url()).mock(return_value=httpx.Response(429, json={"error": "rate"}))
    with pytest.raises(LLMError) as exc_info:
        await llm_mod.chat([{"role": "user", "content": "hi"}])
    # After exhausting primary retries AND fallback retries → exhausted wrapper
    assert exc_info.value.stage == "all_models_exhausted"


@respx.mock
async def test_chat_json_falls_back_when_primary_raises() -> None:
    calls = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["i"] += 1
        if calls["i"] == 1:
            return httpx.Response(500)
        return httpx.Response(200, json=_resp('{"answer": "yes", "score": 1}'))

    respx.post(_chat_url()).mock(side_effect=handler)
    result = await llm_mod.chat_json([{"role": "user", "content": "q"}], _Schema)
    assert result.answer == "yes"
    assert result.score == 1
    assert calls["i"] == 2


@respx.mock
async def test_a_429_is_retried_on_the_same_tier_then_succeeds(_no_sleep) -> None:  # type: ignore[no-untyped-def]
    calls = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["i"] += 1
        if calls["i"] < 3:
            return httpx.Response(429, json={"error": "rate"})
        return httpx.Response(200, json=_resp("success-after-retry"))

    respx.post(_chat_url()).mock(side_effect=handler)
    out = await llm_mod.chat([{"role": "user", "content": "hi"}])
    assert out == "success-after-retry"
    assert calls["i"] == 3


@respx.mock
async def test_chat_uses_fast_tier_when_requested() -> None:
    respx.post(_chat_url()).mock(return_value=httpx.Response(200, json=_resp("fast-tier")))
    out = await llm_mod.chat([{"role": "user", "content": "q"}], model="fast")
    assert out == "fast-tier"


# ---------------------------------------------------------------------------
# JSON replies from the free models (spec 04, 2026-10-20)
# ---------------------------------------------------------------------------

_PLAN_LIKE = '{"answer": "yes", "score": 3}'


@pytest.mark.parametrize(
    "reply",
    [
        f"[{_PLAN_LIKE}]",
        f"```json\n{_PLAN_LIKE}\n```",
        f"```\n{_PLAN_LIKE}\n```",
        f"  ```json\n[{_PLAN_LIKE}]\n```  ",
    ],
    ids=["J1-one-element-array", "J2-json-fence", "J2-bare-fence", "J1+J2-fenced-array"],
)
def test_parse_json_reply_accepts_the_shapes_free_models_send(reply: str) -> None:
    result = llm_mod.parse_json_reply(reply, _Schema)
    assert result == _Schema(answer="yes", score=3)


@pytest.mark.parametrize(
    ("reply", "stage"),
    [
        ("[]", "schema_mismatch"),
        ("[1]", "schema_mismatch"),
        (f"[{_PLAN_LIKE}, {_PLAN_LIKE}]", "schema_mismatch"),
        ('{"wrong": "field"}', "schema_mismatch"),
        ("not json at all", "invalid_json"),
        ("```json\nnot json\n```", "invalid_json"),
    ],
    ids=["J5-empty", "J5-non-object", "J5-two-objects", "mismatch", "invalid", "fenced-invalid"],
)
def test_parse_json_reply_rejects_what_it_cannot_read(reply: str, stage: str) -> None:
    with pytest.raises(LLMError) as exc:
        llm_mod.parse_json_reply(reply, _Schema)
    assert exc.value.stage == stage


@respx.mock
async def test_j1_chat_json_unwraps_the_live_plan_reply_without_a_fallback() -> None:
    route = respx.post(_chat_url()).mock(
        return_value=httpx.Response(200, json=_resp(f"[{_PLAN_LIKE}]"))
    )
    result = await llm_mod.chat_json([{"role": "user", "content": "q"}], _Schema)
    assert result == _Schema(answer="yes", score=3)
    assert route.call_count == 1


@pytest.mark.parametrize(
    "bad_primary",
    ["not json", '{"wrong": "field"}', f"[{_PLAN_LIKE}, {_PLAN_LIKE}]"],
    ids=["invalid-json", "schema-mismatch", "two-objects"],
)
@respx.mock
async def test_j3_unusable_primary_reply_asks_the_fallback_once(bad_primary: str) -> None:
    models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        models.append(_json.loads(request.content)["model"])
        reply = bad_primary if len(models) == 1 else _PLAN_LIKE
        return httpx.Response(200, json=_resp(reply))

    respx.post(_chat_url()).mock(side_effect=handler)
    result = await llm_mod.chat_json([{"role": "user", "content": "q"}], _Schema)
    assert result == _Schema(answer="yes", score=3)
    assert models == [settings.openrouter_model_primary, settings.openrouter_model_fast]


@pytest.mark.parametrize(
    ("fallback_reply", "stage"),
    [("still not json", "invalid_json"), ('{"wrong": 1}', "schema_mismatch")],
)
@respx.mock
async def test_j4_every_reply_unusable_exhausts_the_cascade(
    fallback_reply: str, stage: str
) -> None:
    replies = iter(['{"wrong": "field"}', "not json", fallback_reply])
    route = respx.post(_chat_url()).mock(
        side_effect=lambda _req: httpx.Response(200, json=_resp(next(replies)))
    )
    with pytest.raises(LLMError) as exc:
        await llm_mod.chat_json([{"role": "user", "content": "q"}], _Schema)
    assert exc.value.stage == "all_models_exhausted"
    assert exc.value.detail == f"primary=schema_mismatch; fast=invalid_json; fallback={stage}"
    assert route.call_count == 3


# ---------------------------------------------------------------------------
# The cascade (spec 04, 2026-10-21): PRIMARY -> FAST -> FALLBACK on 429/5xx
# ---------------------------------------------------------------------------


def _by_model(replies: dict[str, Any]) -> tuple[list[str], Any]:
    """A respx handler answering per model; returns (models called, handler).

    A reply is an httpx.Response, an exception to raise, or a list of either,
    consumed one per call (the last one repeats).
    """
    models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        models.append(model)
        reply = replies[model]
        if isinstance(reply, list):
            reply = reply.pop(0) if len(reply) > 1 else reply[0]
        if isinstance(reply, BaseException):
            raise reply
        return reply

    return models, handler


def _ok(content: str) -> httpx.Response:
    return httpx.Response(200, json=_resp(content))


@respx.mock
async def test_c1_504_on_every_primary_attempt_cascades_to_the_fast_tier(
    _no_sleep: list[float],
) -> None:
    models, handler = _by_model(
        {"p/primary:free": httpx.Response(504), "f/fast:free": _ok("from fast")}
    )
    respx.post(_chat_url()).mock(side_effect=handler)
    assert await llm_mod.chat([{"role": "user", "content": "q"}]) == "from fast"
    assert models == ["p/primary:free"] * 5 + ["f/fast:free"]
    assert len(_no_sleep) == 4  # backoff between the five primary attempts


@pytest.mark.parametrize("status", [500, 502, 503, 504])
@respx.mock
async def test_c2_every_tier_5xx_exhausts_after_retrying_each(status: int) -> None:
    models, handler = _by_model(
        {m: httpx.Response(status) for m in ("p/primary:free", "f/fast:free", "b/fallback:free")}
    )
    respx.post(_chat_url()).mock(side_effect=handler)
    with pytest.raises(LLMError) as exc:
        await llm_mod.chat([{"role": "user", "content": "q"}])
    assert exc.value.stage == "all_models_exhausted"
    assert exc.value.detail == "primary=server_error; fast=server_error; fallback=server_error"
    assert models == ["p/primary:free"] * 5 + ["f/fast:free"] * 5 + ["b/fallback:free"] * 5


@respx.mock
async def test_c3_a_timeout_counts_as_a_5xx() -> None:
    models, handler = _by_model(
        {"p/primary:free": httpx.ReadTimeout("slow"), "f/fast:free": _ok("from fast")}
    )
    respx.post(_chat_url()).mock(side_effect=handler)
    assert await llm_mod.chat([{"role": "user", "content": "q"}]) == "from fast"
    assert models == ["p/primary:free"] * 5 + ["f/fast:free"]


@respx.mock
async def test_c4_a_transient_504_is_retried_on_the_same_tier_first(
    _no_sleep: list[float],
) -> None:
    models, handler = _by_model({"p/primary:free": [httpx.Response(504), _ok("primary again")]})
    respx.post(_chat_url()).mock(side_effect=handler)
    assert await llm_mod.chat([{"role": "user", "content": "q"}]) == "primary again"
    assert models == ["p/primary:free", "p/primary:free"]
    assert len(_no_sleep) == 1


@respx.mock
async def test_c5_a_400_moves_on_without_a_retry(_no_sleep: list[float]) -> None:
    models, handler = _by_model(
        {"p/primary:free": httpx.Response(400), "f/fast:free": _ok("from fast")}
    )
    respx.post(_chat_url()).mock(side_effect=handler)
    assert await llm_mod.chat([{"role": "user", "content": "q"}]) == "from fast"
    assert models == ["p/primary:free", "f/fast:free"]
    assert _no_sleep == []


@respx.mock
async def test_c6_chat_json_cascades_on_504_too() -> None:
    models, handler = _by_model(
        {"p/primary:free": httpx.Response(504), "f/fast:free": _ok(_PLAN_LIKE)}
    )
    respx.post(_chat_url()).mock(side_effect=handler)
    result = await llm_mod.chat_json([{"role": "user", "content": "q"}], _Schema)
    assert result == _Schema(answer="yes", score=3)
    assert models[-1] == "f/fast:free"


@respx.mock
async def test_c7_a_fast_request_cascades_fast_then_primary_then_fallback() -> None:
    models, handler = _by_model(
        {m: httpx.Response(502) for m in ("p/primary:free", "f/fast:free", "b/fallback:free")}
    )
    respx.post(_chat_url()).mock(side_effect=handler)
    with pytest.raises(LLMError) as exc:
        await llm_mod.chat([{"role": "user", "content": "q"}], model="fast")
    assert exc.value.detail == "fast=server_error; primary=server_error; fallback=server_error"
    assert models == ["f/fast:free"] * 5 + ["p/primary:free"] * 5 + ["b/fallback:free"] * 5


@respx.mock
async def test_c8_a_model_two_tiers_share_is_called_as_one_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openrouter_model_fallback", "f/fast:free")
    models, handler = _by_model(
        {"p/primary:free": httpx.Response(502), "f/fast:free": httpx.Response(504)}
    )
    respx.post(_chat_url()).mock(side_effect=handler)
    with pytest.raises(LLMError) as exc:
        await llm_mod.chat([{"role": "user", "content": "q"}])
    assert exc.value.detail == "primary=server_error; fast=server_error"
    assert models == ["p/primary:free"] * 5 + ["f/fast:free"] * 5


async def test_c9_hanging_tiers_are_cut_at_the_node_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "llm_node_deadline_s", 0.3)
    called: list[str] = []

    async def _hang(messages: Any, model: str, temperature: float, *, json_mode: bool) -> str:
        called.append(model)
        await asyncio.sleep(10)
        return "never"

    monkeypatch.setattr(llm_mod, "_one_call", _hang)
    started = time.monotonic()
    with pytest.raises(LLMError) as exc:
        await llm_mod.chat_json([{"role": "user", "content": "q"}], _Schema)
    assert time.monotonic() - started < 1.0
    assert exc.value.stage == "all_models_exhausted"
    assert exc.value.detail == "primary=timeout; fast=timeout; fallback=timeout"
    assert called == ["p/primary:free", "f/fast:free", "b/fallback:free"]


@respx.mock
async def test_c10_a_backoff_that_outlasts_the_tiers_share_is_not_slept(
    monkeypatch: pytest.MonkeyPatch, _no_sleep: list[float]
) -> None:
    # 3 s for three tiers: 1 s each, shorter than the first 1.5 s backoff.
    monkeypatch.setattr(settings, "llm_node_deadline_s", 3.0)
    models, handler = _by_model(
        {"p/primary:free": httpx.Response(503), "f/fast:free": _ok("from fast")}
    )
    respx.post(_chat_url()).mock(side_effect=handler)
    assert await llm_mod.chat([{"role": "user", "content": "q"}]) == "from fast"
    assert models == ["p/primary:free", "f/fast:free"]
    assert _no_sleep == []


def test_c_cascade_order_is_pure_and_skips_repeated_models() -> None:
    assert llm_mod.cascade_order("primary", {"primary": "a", "fast": "b", "fallback": "c"}) == [
        ("primary", "a"),
        ("fast", "b"),
        ("fallback", "c"),
    ]
    assert llm_mod.cascade_order("fallback", {"primary": "a", "fast": "b", "fallback": "c"}) == [
        ("fallback", "c"),
        ("primary", "a"),
        ("fast", "b"),
    ]
    assert llm_mod.cascade_order("primary", {"primary": "a", "fast": "a", "fallback": "a"}) == [
        ("primary", "a")
    ]


async def test_c11_a_tier_that_starts_with_no_time_left_makes_no_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []

    async def _never(messages: Any, model: str, temperature: float, *, json_mode: bool) -> str:
        called.append(model)
        return "never"

    monkeypatch.setattr(llm_mod, "_one_call", _never)
    already_over = asyncio.get_running_loop().time() - 1.0
    with pytest.raises(LLMError) as exc:
        await llm_mod._call_tier([], "p/primary:free", 0.2, json_mode=False, until=already_over)
    assert exc.value.stage == "timeout"
    assert called == []
