"""Langfuse observability wrapper — SDK v3+ (``langfuse>=3``).

Behavioural contract: tracing failures must NEVER fail requests. Everything
here is best-effort; on any langfuse-side exception we log a warning via
structlog and continue. If the configured langfuse keys are blank, or if
constructing the SDK client raises, every helper becomes a no-op.

## Public API

- ``span(name, input=..., metadata=..., as_type='span'|'generation')`` — an
  async context manager that opens a Langfuse observation rooted at the
  current OTel context. Yields a handle you can call ``.update(output=...,
  metadata=...)`` on, or ``None`` when tracing is disabled. Child spans
  created inside this context are automatically nested.
- ``update_current_span(output=..., metadata=...)`` — mutate the currently
  active observation (e.g. set its output after the fact).
- ``update_current_trace(name=..., input=..., output=..., tags=...,
  metadata=..., user_id=..., session_id=...)`` — mutate the root trace,
  callable from any descendant span.
- ``trace(name)`` — async decorator that wraps a coroutine in a top-level
  ``span(name)``. Kept for compatibility; new code should prefer the
  context manager directly so it can pass input/output.
- ``trace_event(name, **data)`` — best-effort standalone event.
"""

from __future__ import annotations

import functools
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal, TypeVar

import structlog

from paper_trail.core.config import settings

try:  # pragma: no cover - import-time fallback
    from langfuse import Langfuse, LangfuseOtelSpanAttributes
except Exception:  # pragma: no cover
    Langfuse = None  # type: ignore[assignment,misc]
    LangfuseOtelSpanAttributes = None  # type: ignore[assignment,misc]

try:  # pragma: no cover
    from opentelemetry import trace as _otel_trace
except Exception:  # pragma: no cover
    _otel_trace = None  # type: ignore[assignment]


def _json_dump(value: Any) -> str:
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except Exception:
        return str(value)


_logger = structlog.get_logger(__name__)

_client: Any = None
_client_initialized: bool = False

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def _get_client() -> Any:
    global _client, _client_initialized
    if _client_initialized:
        return _client
    _client_initialized = True
    pk = getattr(settings, "langfuse_public_key", "") or ""
    sk = getattr(settings, "langfuse_secret_key", "") or ""
    host = getattr(settings, "langfuse_base_url", "") or ""
    if not pk or not sk or Langfuse is None:
        _client = None
        return None
    try:
        kwargs: dict[str, Any] = {"public_key": pk, "secret_key": sk}
        if host:
            kwargs["host"] = host
        _client = Langfuse(**kwargs)
    except Exception as exc:
        _logger.warning("langfuse_init_failed", error=str(exc))
        _client = None
    return _client


def _safe_flush(client: Any) -> None:
    try:
        client.flush()
    except Exception as exc:
        _logger.warning("langfuse_flush_failed", error=str(exc))


@asynccontextmanager
async def span(
    name: str,
    *,
    input: Any = None,
    metadata: dict[str, Any] | None = None,
    as_type: Literal["span", "generation"] = "span",
) -> AsyncIterator[Any]:
    """Open a Langfuse observation; fail-safe no-op when disabled.

    Yields a Langfuse observation handle you can ``.update(output=...,
    metadata=...)`` on, or ``None`` if tracing is disabled. The surrounding
    context manager flushes on exit so short-lived request-scoped spans
    are pushed to the dashboard immediately.
    """
    client = _get_client()
    if client is None:
        yield None
        return

    kwargs: dict[str, Any] = {"name": name, "as_type": as_type}
    if input is not None:
        kwargs["input"] = input
    if metadata is not None:
        kwargs["metadata"] = metadata

    ctx: Any = None
    handle: Any = None
    try:
        ctx = client.start_as_current_observation(**kwargs)
        handle = ctx.__enter__()
    except Exception as exc:
        _logger.warning("langfuse_span_start_failed", name=name, error=str(exc))
        yield None
        return

    try:
        yield handle
    except BaseException:
        try:
            ctx.__exit__(None, None, None)
        except Exception as exc:
            _logger.warning("langfuse_span_exit_failed", name=name, error=str(exc))
        _safe_flush(client)
        raise

    try:
        ctx.__exit__(None, None, None)
    except Exception as exc:
        _logger.warning("langfuse_span_exit_failed", name=name, error=str(exc))
    _safe_flush(client)


def update_current_span(
    *,
    output: Any = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Best-effort update of the currently active observation."""
    client = _get_client()
    if client is None:
        return
    kw: dict[str, Any] = {}
    if output is not None:
        kw["output"] = output
    if metadata is not None:
        kw["metadata"] = metadata
    if not kw:
        return
    try:
        client.update_current_span(**kw)
    except Exception as exc:
        _logger.warning("langfuse_update_span_failed", error=str(exc))


def update_current_trace(
    *,
    name: str | None = None,
    input: Any = None,
    output: Any = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """Best-effort update of the root trace associated with the current span.

    Langfuse SDK v3 represents the trace as the root OTel span; trace-level
    fields (name, tags, user/session id, metadata) are carried as specific
    OTel attributes on the currently-active span, which Langfuse's exporter
    reads and promotes to trace fields at ingest time. There is no direct
    client-level ``update_current_trace`` method in v3 (it existed in v2 as
    ``langfuse_context.update_current_trace`` but was removed); this helper
    talks to OTel directly using the attribute keys Langfuse documents.
    """
    if _get_client() is None or LangfuseOtelSpanAttributes is None or _otel_trace is None:
        return
    span_obj = _otel_trace.get_current_span()
    if span_obj is None or not span_obj.is_recording():
        return
    attrs = LangfuseOtelSpanAttributes
    try:
        if name is not None:
            span_obj.set_attribute(attrs.TRACE_NAME, name)
        if input is not None:
            span_obj.set_attribute(attrs.TRACE_INPUT, _json_dump(input))
        if output is not None:
            span_obj.set_attribute(attrs.TRACE_OUTPUT, _json_dump(output))
        if tags is not None:
            # OTel sequence attrs accept list[str] directly.
            span_obj.set_attribute(attrs.TRACE_TAGS, list(tags))
        if metadata is not None:
            span_obj.set_attribute(attrs.TRACE_METADATA, _json_dump(metadata))
        if user_id is not None:
            span_obj.set_attribute(attrs.TRACE_USER_ID, user_id)
        if session_id is not None:
            span_obj.set_attribute(attrs.TRACE_SESSION_ID, session_id)
    except Exception as exc:
        _logger.warning("langfuse_update_trace_failed", error=str(exc))


def trace(name: str) -> Callable[[F], F]:
    """Async decorator that wraps a coroutine in a top-level span.

    New code should prefer ``span(...)`` directly so it can capture
    input/output and set trace metadata from inside the body.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            async with span(name):
                return await func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def trace_event(name: str, **data: Any) -> None:
    """Best-effort standalone event. Swallows all errors."""
    try:
        client = _get_client()
        if client is None:
            return
        try:
            client.create_event(name=name, metadata=data)
        except Exception as exc:
            _logger.warning("langfuse_event_failed", name=name, error=str(exc))
    except Exception as exc:  # pragma: no cover - extra safety
        _logger.warning("langfuse_event_failed", name=name, error=str(exc))
