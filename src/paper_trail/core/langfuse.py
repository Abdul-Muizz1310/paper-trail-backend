"""Langfuse trace wrapper.

Behavioural contract: tracing failures must NEVER fail requests. Everything
here is best-effort; on any langfuse-side exception we log a warning via
structlog and continue. If the configured langfuse keys are blank, or if
constructing the SDK client raises, the decorator/event helpers become
no-ops while still running the wrapped function.
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


def trace(name: str) -> Callable[[F], F]:
    """Async decorator that wraps a coroutine in a langfuse trace.

    On any langfuse-side failure the wrapped function's result (or exception)
    is preserved untouched — the trace layer can never alter call semantics.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            client = _get_client()
            trace_obj: Any = None
            if client is not None:
                try:
                    trace_obj = client.trace(name=name)
                except Exception as exc:
                    _logger.warning("langfuse_trace_failed", name=name, error=str(exc))
                    trace_obj = None
            try:
                result = await func(*args, **kwargs)
            except BaseException:
                if trace_obj is not None:
                    try:
                        trace_obj.update(status_message="error")
                        trace_obj.end()
                    except Exception as exc:
                        _logger.warning("langfuse_trace_failed", name=name, error=str(exc))
                raise
            if trace_obj is not None:
                try:
                    trace_obj.update(status_message="ok")
                    trace_obj.end()
                except Exception as exc:
                    _logger.warning("langfuse_trace_failed", name=name, error=str(exc))
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
            client.event(name=name, metadata=data)
        except Exception as exc:
            _logger.warning("langfuse_event_failed", name=name, error=str(exc))
    except Exception as exc:  # pragma: no cover - extra safety
        _logger.warning("langfuse_event_failed", name=name, error=str(exc))
