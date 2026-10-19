"""Unit tests for core/llm.py (OpenRouter router with fallback)."""

from __future__ import annotations

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


@pytest.fixture(autouse=False)
def _no_sleep(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Skip real sleeps so retry loops finish instantly."""

    async def _noop(_seconds: float) -> None:
        return None

    monkeypatch.setattr(llm_mod.asyncio, "sleep", _noop)
    return _noop


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
async def test_one_call_with_retry_retries_on_429_then_succeeds(_no_sleep) -> None:  # type: ignore[no-untyped-def]
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
    assert models == [settings.openrouter_model_primary, settings.openrouter_model_fallback]


@pytest.mark.parametrize(
    ("fallback_reply", "stage"),
    [("still not json", "invalid_json"), ('{"wrong": 1}', "schema_mismatch")],
)
@respx.mock
async def test_j4_both_replies_unusable_raises_the_fallbacks_stage(
    fallback_reply: str, stage: str
) -> None:
    replies = iter(['{"wrong": "field"}', fallback_reply])
    route = respx.post(_chat_url()).mock(
        side_effect=lambda _req: httpx.Response(200, json=_resp(next(replies)))
    )
    with pytest.raises(LLMError) as exc:
        await llm_mod.chat_json([{"role": "user", "content": "q"}], _Schema)
    assert exc.value.stage == stage
    assert route.call_count == 2
