"""LLM router over OpenRouter: a PRIMARY -> FAST -> FALLBACK cascade with JSON mode.

docs/specs/04-llm-router.md, "The cascade": transient failures (429, any 5xx,
a timeout, a dropped connection) are retried on the same tier with jittered
exponential backoff, then the next tier is asked; any other failure moves on at
once. ``all_models_exhausted`` only after every tier has failed. One call is
bounded by ``settings.llm_node_deadline_s`` (each tier gets an equal share of
what is left), so a node cannot hang on a dead upstream.

Every HTTP attempt is one Langfuse generation named by the caller
(docs/specs/12-tracing.md): the model it asked, the messages, the reply, token
usage and cost, and level ERROR when the attempt failed.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Any, Literal, TypedDict

import httpx
from langfuse import LangfuseGeneration
from langfuse.api import MapValue
from pydantic import BaseModel, BeforeValidator, Field, ValidationError

from paper_trail.core import tracing
from paper_trail.core.config import settings
from paper_trail.core.errors import LLMError

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

#: The generation each caller records (docs/specs/12-tracing.md): plan, proponent and
#: skeptic, judge. Stable names: evaluators and dashboards target them.
GenerationName = Literal["decompose-claim", "write-argument", "score-round"]


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


def _count(value: object) -> int | None:
    """A token count OpenRouter reported, or ``None`` unless it is a non-negative int."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _cost(value: object) -> float | None:
    """A cost OpenRouter reported, or ``None`` unless it is a finite, non-negative number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    cost = float(value)
    return cost if math.isfinite(cost) and cost >= 0 else None


def _mapping_or_none(value: object) -> object:
    return value if isinstance(value, Mapping) else None


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


Count = Annotated[int | None, BeforeValidator(_count)]
Cost = Annotated[float | None, BeforeValidator(_cost)]
Text = Annotated[str | None, BeforeValidator(_str_or_none)]


class _Details(BaseModel):
    cached_tokens: Count = None
    reasoning_tokens: Count = None


class _Usage(BaseModel):
    """Telemetry only: a field that does not read is dropped, never fatal to the call."""

    prompt_tokens: Count = None
    completion_tokens: Count = None
    cost: Cost = None
    prompt_tokens_details: Annotated[_Details | None, BeforeValidator(_mapping_or_none)] = None
    completion_tokens_details: Annotated[_Details | None, BeforeValidator(_mapping_or_none)] = None


class _Message(BaseModel):
    content: str | None = None
    reasoning: Text = None


class _Choice(BaseModel):
    message: _Message


class _Reply(BaseModel):
    """The part of an OpenRouter chat completion the router reads."""

    provider: Text = None
    choices: list[_Choice] = Field(min_length=1)
    usage: Annotated[_Usage | None, BeforeValidator(_mapping_or_none)] = None


def _read_reply(resp: httpx.Response) -> _Reply:
    """Parse a 200; one without a completion is ``bad_response`` (spec 04, rule 3)."""
    try:
        reply = _Reply.model_validate(resp.json())
    except (ValueError, ValidationError) as e:
        raise LLMError("bad_response", str(e)) from e
    content = reply.choices[0].message.content
    if content is None or not content.strip():
        raise LLMError("bad_response", "no completion")
    return reply


def _record_reply(generation: LangfuseGeneration, reply: _Reply) -> None:
    message = reply.choices[0].message
    output: dict[str, str] = {"role": "assistant", "content": message.content or ""}
    if message.reasoning:
        output["reasoning"] = message.reasoning
    usage = reply.usage or _Usage()
    prompt_details = usage.prompt_tokens_details or _Details()
    completion_details = usage.completion_tokens_details or _Details()
    buckets = tracing.usage_details(
        prompt=usage.prompt_tokens,
        completion=usage.completion_tokens,
        cached=prompt_details.cached_tokens,
        reasoning=completion_details.reasoning_tokens,
    )
    generation.update(
        output=output,
        usage_details=buckets or None,
        cost_details=tracing.cost_details(usage.cost),
        metadata={"provider": reply.provider} if reply.provider else None,
    )


async def _one_call(
    messages: list[ChatMessage],
    model: str,
    temperature: float,
    *,
    json_mode: bool,
    name: GenerationName,
    tier: ModelTier,
    attempt: int,
) -> str:
    url = f"{settings.openrouter_base_url}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "temperature": temperature,
    }
    parameters: dict[str, MapValue] = {"temperature": temperature}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
        parameters["response_format"] = "json_object"
    with tracing.client().start_as_current_observation(
        as_type="generation",
        name=name,
        model=model,
        input=list(messages),
        model_parameters=parameters,
        metadata={"tier": tier, "attempt": attempt},
    ) as generation:
        try:
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
            reply = _read_reply(resp)
        except LLMError as e:
            generation.update(level="ERROR", status_message=str(e))
            raise
        except asyncio.CancelledError:
            # The tier's time share or the debate deadline ended the attempt.
            generation.update(level="ERROR", status_message="cancelled before the model answered")
            raise
        _record_reply(generation, reply)
        return reply.choices[0].message.content or ""


def _backoff_s(attempt: int) -> float:
    return _BACKOFF_BASE_S * float(1 << attempt) + random.random() * _BACKOFF_JITTER_S


async def _call_tier(
    messages: list[ChatMessage],
    model: str,
    temperature: float,
    *,
    json_mode: bool,
    name: GenerationName,
    tier: ModelTier,
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
                return await _one_call(
                    messages,
                    model,
                    temperature,
                    json_mode=json_mode,
                    name=name,
                    tier=tier,
                    attempt=attempt + 1,
                )
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
    name: GenerationName,
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
                messages,
                model,
                temperature,
                json_mode=json_mode,
                name=name,
                tier=tier,
                until=loop.time() + share,
            )
            return read(raw)
        except LLMError as exc:
            failures.append(f"{tier}={exc.stage}")
            last = exc
    raise LLMError("all_models_exhausted", "; ".join(failures)) from last


async def chat(
    messages: list[ChatMessage],
    *,
    name: GenerationName,
    model: ModelTier = "primary",
    temperature: float = 0.2,
) -> str:
    """Call OpenRouter through the cascade, starting at tier ``model``.

    ``name`` names the generation each attempt records (docs/specs/12-tracing.md).
    """
    return await _cascade(
        messages, model, temperature, json_mode=False, name=name, read=lambda raw: raw
    )


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
    name: GenerationName,
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
        name=name,
        read=lambda raw: parse_json_reply(raw, schema),
    )
