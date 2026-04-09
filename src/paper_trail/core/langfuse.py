"""Langfuse trace wrapper — SDK v3 API (``langfuse>=3``).

Behavioural contract: tracing failures must NEVER fail requests. Everything
here is best-effort; on any langfuse-side exception we log a warning via
structlog and continue. If the configured langfuse keys are blank, or if
constructing the SDK client raises, the decorator/event helpers become
no-ops while still running the wrapped function.

Background: the Langfuse Python SDK made a breaking change between v2 and
v3+. v2 exposed ``client.trace(name=...)`` returning a mutable trace object;
v3 replaced that with an OTel-backed span API using
``client.start_as_current_observation(name=..., as_type='span')`` as a
context manager. This module targets v3 (the version installed via
``langfuse>=2.55`` at this writing resolves to 4.x).
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import structlog

from paper_trail.core.config import settings

try:  # pragma: no cover - import-time fallback
    from langfuse import Langfuse
except Exception:  # pragma: no cover
    Langfuse = None  # type: ignore[assignment,misc]

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


def trace(name: str) -> Callable[[F], F]:
    """Async decorator that wraps a coroutine in a langfuse span.

    The span is entered before the wrapped call and exited after; on exit we
    flush the client so traces are pushed immediately (relevant for short,
    request-scoped lifetimes). On any langfuse-side failure the wrapped
    function's result (or exception) is preserved untouched — the trace
    layer can never alter call semantics.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            client = _get_client()
            if client is None:
                return await func(*args, **kwargs)

            span_ctx: Any = None
            try:
                span_ctx = client.start_as_current_observation(name=name, as_type="span")
                span_ctx.__enter__()
            except Exception as exc:
                _logger.warning("langfuse_span_start_failed", name=name, error=str(exc))
                span_ctx = None

            try:
                result = await func(*args, **kwargs)
            except BaseException:
                if span_ctx is not None:
                    try:
                        span_ctx.__exit__(None, None, None)
                    except Exception as exc:
                        _logger.warning("langfuse_span_exit_failed", name=name, error=str(exc))
                _safe_flush(client)
                raise

            if span_ctx is not None:
                try:
                    span_ctx.__exit__(None, None, None)
                except Exception as exc:
                    _logger.warning("langfuse_span_exit_failed", name=name, error=str(exc))
            _safe_flush(client)
            return result

        return wrapper  # type: ignore[return-value]

    return decorator


def trace_event(name: str, **data: Any) -> None:
    """Best-effort event log. Swallows all errors."""
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
