"""Langfuse tracing: Langfuse v4 through Python SDK v5 (docs/specs/12-tracing.md).

A debate is one trace. ``debate_trace`` opens its root observation, ``run-debate``,
and gives every observation under it the trace attributes; nodes and tools open
their own observations on ``client()``; the LLM router records each attempt as a
generation, with ``usage_details`` and ``cost_details`` turning OpenRouter's
counts into Langfuse's buckets. With no Langfuse keys every observation is a
no-op and nothing is exported.

Export runs in the SDK's background batches; nothing here flushes on the request
path. ``shutdown`` flushes what is left when the app stops.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal
from uuid import UUID

from langfuse import Langfuse, LangfuseAgent, propagate_attributes
from slowquery_detective import current_request_id

from paper_trail.core.config import Settings, settings

SERVICE_TAG = "paper-trail"

#: What started a debate: ``POST /debates``, ``POST /platform/debate``, or the eval runner.
Entry = Literal["api", "platform", "eval"]

_client: Langfuse | None = None


def build_client(cfg: Settings) -> Langfuse:
    """A client for ``cfg``: tracing on with both keys, a no-op without either."""
    if not (cfg.langfuse_public_key and cfg.langfuse_secret_key):
        return Langfuse(tracing_enabled=False)
    return Langfuse(
        public_key=cfg.langfuse_public_key,
        secret_key=cfg.langfuse_secret_key,
        base_url=cfg.langfuse_base_url or None,
        environment=cfg.app_env,
    )


def client() -> Langfuse:
    """The process's client, built from the settings on first use."""
    global _client
    if _client is None:
        _client = build_client(settings)
    return _client


def shutdown() -> None:
    """Flush buffered observations and stop the exporter (app shutdown, end of a script)."""
    if _client is not None:
        _client.shutdown()


@contextmanager
def debate_trace(
    *, debate_id: UUID, claim: str, max_rounds: int, entry: Entry
) -> Iterator[LangfuseAgent]:
    """Open ``run-debate`` and give every observation under it the trace attributes.

    The session is the request's ``X-Request-Id`` (as slowquery-detective validated it):
    bastion sends one id for every call of a dossier, so its traces group into one session.
    """
    with (
        client().start_as_current_observation(
            as_type="agent",
            name="run-debate",
            input={"claim": claim, "max_rounds": max_rounds},
        ) as root,
        propagate_attributes(
            trace_name="run-debate",
            session_id=current_request_id(),
            tags=[SERVICE_TAG, f"entry:{entry}"],
            metadata={"debate_id": str(debate_id)},
        ),
    ):
        yield root


def usage_details(
    *,
    prompt: int | None = None,
    completion: int | None = None,
    cached: int | None = None,
    reasoning: int | None = None,
) -> dict[str, int]:
    """OpenRouter's token counts as Langfuse's exclusive usage buckets.

    OpenRouter counts cached tokens inside ``prompt`` and reasoning tokens inside
    ``completion``; Langfuse adds its buckets up, so each detail is taken out of its
    total (and never more than the total). A total that was not reported drops its
    buckets, details included.
    """
    buckets: dict[str, int] = {}
    if prompt is not None:
        hit = min(cached or 0, prompt)
        buckets["input"] = prompt - hit
        if hit:
            buckets["input_cached_tokens"] = hit
    if completion is not None:
        thought = min(reasoning or 0, completion)
        buckets["output"] = completion - thought
        if thought:
            buckets["output_reasoning_tokens"] = thought
    return buckets


def cost_details(cost: float | None) -> dict[str, float] | None:
    """The cost OpenRouter reported for a call, as the generation's total (USD)."""
    return None if cost is None else {"total": cost}
