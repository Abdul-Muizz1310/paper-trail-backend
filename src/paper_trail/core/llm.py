"""LLM router over OpenRouter with primary→fallback retry and JSON mode."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel

ModelTier = Literal["primary", "fast", "fallback"]


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


async def chat(
    messages: list[ChatMessage],
    *,
    model: ModelTier = "primary",
    temperature: float = 0.2,
) -> str:
    raise NotImplementedError


async def chat_json(
    messages: list[ChatMessage],
    schema: type[BaseModel],
    *,
    model: ModelTier = "primary",
    temperature: float = 0.2,
) -> Any:
    raise NotImplementedError
