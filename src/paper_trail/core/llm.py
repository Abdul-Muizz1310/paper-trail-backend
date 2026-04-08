"""LLM router over OpenRouter with primary->fallback retry and JSON mode."""

from __future__ import annotations

import json
from typing import Any, Literal, TypedDict

import httpx
from pydantic import BaseModel, ValidationError

from paper_trail.core.config import settings
from paper_trail.core.errors import LLMError

ModelTier = Literal["primary", "fast", "fallback"]


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


def _resolve_model(tier: ModelTier) -> str:
    mapping = {
        "primary": settings.openrouter_model_primary,
        "fast": settings.openrouter_model_fast,
        "fallback": settings.openrouter_model_fallback,
    }
    return mapping[tier]


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": settings.openrouter_http_referer,
        "X-Title": settings.openrouter_x_title,
    }


async def _one_call(
    messages: list[ChatMessage],
    model: str,
    temperature: float,
    *,
    json_mode: bool,
) -> str:
    url = f"{settings.openrouter_base_url}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=settings.openrouter_timeout_s) as client:
        try:
            resp = await client.post(url, headers=_headers(), json=payload)
        except httpx.TimeoutException as e:
            raise LLMError("timeout", str(e)) from e
        except httpx.HTTPError as e:
            raise LLMError("http_error", str(e)) from e
    if resp.status_code == 429:
        raise LLMError("rate_limited", f"status={resp.status_code}")
    if resp.status_code >= 500:
        raise LLMError("server_error", f"status={resp.status_code}")
    if resp.status_code >= 400:
        raise LLMError("client_error", f"status={resp.status_code}")
    data = resp.json()
    try:
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError("bad_response", str(e)) from e


async def chat(
    messages: list[ChatMessage],
    *,
    model: ModelTier = "primary",
    temperature: float = 0.2,
) -> str:
    """Call OpenRouter with primary->fallback retry."""
    try:
        return await _one_call(messages, _resolve_model(model), temperature, json_mode=False)
    except LLMError as primary_err:
        try:
            return await _one_call(
                messages,
                _resolve_model("fallback"),
                temperature,
                json_mode=False,
            )
        except LLMError as fallback_err:
            raise LLMError(
                "all_models_exhausted",
                f"primary={primary_err.detail}; fallback={fallback_err.detail}",
            ) from fallback_err


async def chat_json(
    messages: list[ChatMessage],
    schema: type[BaseModel],
    *,
    model: ModelTier = "primary",
    temperature: float = 0.2,
) -> Any:
    """Call OpenRouter in JSON mode and validate against a Pydantic schema."""
    try:
        raw = await _one_call(messages, _resolve_model(model), temperature, json_mode=True)
    except LLMError:
        raw = await _one_call(
            messages,
            _resolve_model("fallback"),
            temperature,
            json_mode=True,
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMError("invalid_json", str(e)) from e
    try:
        return schema.model_validate(data)
    except ValidationError as e:
        raise LLMError("schema_mismatch", str(e)) from e
