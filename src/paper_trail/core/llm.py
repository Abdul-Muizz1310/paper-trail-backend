"""LLM router over OpenRouter: a PRIMARY -> FAST -> FALLBACK cascade with JSON mode.

docs/specs/04-llm-router.md, "The cascade": transient failures (429, any 5xx,
a timeout, a dropped connection) are retried on the same tier with jittered
exponential backoff, then the next tier is asked; any other failure moves on at
once. ``all_models_exhausted`` only after every tier has failed. One call is
bounded by ``settings.llm_node_deadline_s`` (each tier gets an equal share of
what is left), so a node cannot hang on a dead upstream.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, TypedDict

import httpx
from pydantic import BaseModel, ValidationError

from paper_trail.core.config import settings
from paper_trail.core.errors import LLMError
from paper_trail.core.langfuse import span, update_current_span

# Retry policy on one tier. OpenRouter free tiers are bursty (~1-2 req/s per
# upstream) and the proponent/skeptic nodes run in parallel, so without jitter
# they race each other into the same backoff window.
_ATTEMPTS_PER_TIER = 5
_BACKOFF_BASE_S = 1.5
_BACKOFF_JITTER_S = 0.75
# Failures worth retrying on the same model; anything else moves to the next tier.
_TRANSIENT_STAGES = frozenset({"rate_limited", "server_error", "timeout", "http_error"})

# Test seam: backoff sleeps go through here, never through the event loop's own timers.
_backoff_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


ModelTier = Literal["primary", "fast", "fallback"]


# A whole reply wrapped in a Markdown code fence, with or without a language tag.
_FENCED = re.compile(r"^```[A-Za-z]*\s*(.*?)\s*```$", re.DOTALL)


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


_CASCADE: tuple[ModelTier, ...] = ("primary", "fast", "fallback")


def _configured_models() -> dict[ModelTier, str]:
    return {
        "primary": settings.openrouter_model_primary,
        "fast": settings.openrouter_model_fast,
        "fallback": settings.openrouter_model_fallback,
    }


def cascade_order(
    requested: ModelTier, models: Mapping[ModelTier, str]
) -> list[tuple[ModelTier, str]]:
    """The tiers to try: the requested one, then PRIMARY -> FAST -> FALLBACK; a model once."""
    order = [requested, *(tier for tier in _CASCADE if tier != requested)]
    tried: set[str] = set()
    out: list[tuple[ModelTier, str]] = []
    for tier in order:
        model = models[tier]
        if model in tried:
            continue
        tried.add(model)
        out.append((tier, model))
    return out


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
    gen_name = f"llm.{'json' if json_mode else 'chat'}"
    async with span(
        gen_name,
        input={"messages": list(messages)},
        metadata={"model": model, "temperature": temperature, "json_mode": json_mode},
        as_type="generation",
    ):
        async with httpx.AsyncClient(timeout=settings.openrouter_timeout_s) as client:
            try:
                resp = await client.post(url, headers=_headers(), json=payload)
            except httpx.TimeoutException as e:
                update_current_span(metadata={"error": "timeout", "error_detail": str(e)})
                raise LLMError("timeout", str(e)) from e
            except httpx.HTTPError as e:
                update_current_span(metadata={"error": "http_error", "error_detail": str(e)})
                raise LLMError("http_error", str(e)) from e
        if resp.status_code == 429:
            update_current_span(metadata={"error": "rate_limited", "status_code": 429})
            raise LLMError("rate_limited", f"status={resp.status_code}")
        if resp.status_code >= 500:
            update_current_span(metadata={"error": "server_error", "status_code": resp.status_code})
            raise LLMError("server_error", f"status={resp.status_code}")
        if resp.status_code >= 400:
            update_current_span(metadata={"error": "client_error", "status_code": resp.status_code})
            raise LLMError("client_error", f"status={resp.status_code}")
        data = resp.json()
        try:
            completion = str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as e:
            update_current_span(metadata={"error": "bad_response", "error_detail": str(e)})
            raise LLMError("bad_response", str(e)) from e
        usage = data.get("usage") or {}
        update_current_span(
            output=completion,
            metadata={
                "model": model,
                "status_code": resp.status_code,
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
            },
        )
        return completion


def _backoff_s(attempt: int) -> float:
    return _BACKOFF_BASE_S * float(1 << attempt) + random.random() * _BACKOFF_JITTER_S


async def _call_tier(
    messages: list[ChatMessage],
    model: str,
    temperature: float,
    *,
    json_mode: bool,
    until: float,
) -> str:
    """Ask one model until it answers, a non-transient failure, or its time share ends."""
    loop = asyncio.get_running_loop()
    last = LLMError("timeout", "no time left for this tier")
    for attempt in range(_ATTEMPTS_PER_TIER):
        left = until - loop.time()
        if left <= 0:
            break
        try:
            async with asyncio.timeout(left):
                return await _one_call(messages, model, temperature, json_mode=json_mode)
        except TimeoutError:
            last = LLMError("timeout", f"{model}: tier time share spent")
        except LLMError as exc:
            if exc.stage not in _TRANSIENT_STAGES:
                raise
            last = exc
        if attempt == _ATTEMPTS_PER_TIER - 1:
            break
        delay = _backoff_s(attempt)
        if loop.time() + delay >= until:
            break
        await _backoff_sleep(delay)
    raise last


async def _cascade[ResultT](
    messages: list[ChatMessage],
    requested: ModelTier,
    temperature: float,
    *,
    json_mode: bool,
    read: Callable[[str], ResultT],
) -> ResultT:
    """Run the cascade; ``read`` turns a reply into the result or raises ``LLMError``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.llm_node_deadline_s
    tiers = cascade_order(requested, _configured_models())
    failures: list[str] = []
    last: LLMError | None = None
    for index, (tier, model) in enumerate(tiers):
        share = (deadline - loop.time()) / (len(tiers) - index)
        try:
            raw = await _call_tier(
                messages, model, temperature, json_mode=json_mode, until=loop.time() + share
            )
            return read(raw)
        except LLMError as exc:
            failures.append(f"{tier}={exc.stage}")
            last = exc
    raise LLMError("all_models_exhausted", "; ".join(failures)) from last


async def chat(
    messages: list[ChatMessage],
    *,
    model: ModelTier = "primary",
    temperature: float = 0.2,
) -> str:
    """Call OpenRouter through the cascade, starting at tier ``model``."""
    return await _cascade(messages, model, temperature, json_mode=False, read=lambda raw: raw)


def parse_json_reply[SchemaT: BaseModel](raw: str, schema: type[SchemaT]) -> SchemaT:
    """Parse a JSON-mode reply into ``schema`` (spec 04, "JSON replies from the free models").

    Tolerates the two shapes free models send instead of a bare object: a reply
    fenced as a Markdown code block, and the object wrapped in a one-element
    array. Anything else is parsed as is and must match ``schema``; it never
    guesses between several objects.
    """
    text = raw.strip()
    fenced = _FENCED.match(text)
    if fenced is not None:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError("invalid_json", str(e)) from e
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        data = data[0]
    try:
        return schema.model_validate(data)
    except ValidationError as e:
        raise LLMError("schema_mismatch", str(e)) from e


async def chat_json[SchemaT: BaseModel](
    messages: list[ChatMessage],
    schema: type[SchemaT],
    *,
    model: ModelTier = "primary",
    temperature: float = 0.2,
) -> SchemaT:
    """Call OpenRouter in JSON mode through the cascade and validate against ``schema``.

    A reply ``parse_json_reply`` rejects counts as a failed tier, like a 429 or
    a 5xx, but is not retried on the same model: the next tier is asked.
    """
    return await _cascade(
        messages,
        model,
        temperature,
        json_mode=True,
        read=lambda raw: parse_json_reply(raw, schema),
    )
