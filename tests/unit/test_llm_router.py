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
