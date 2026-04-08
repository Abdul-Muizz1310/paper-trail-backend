"""Tests for core.langfuse trace wrapper."""

from __future__ import annotations

import asyncio

import pytest

from paper_trail.core import langfuse as lf_module
from paper_trail.core.config import settings


def _blank_langfuse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "langfuse_public_key", "", raising=False)
    monkeypatch.setattr(settings, "langfuse_secret_key", "", raising=False)
    monkeypatch.setattr(settings, "langfuse_base_url", "", raising=False)
    monkeypatch.setattr(lf_module, "_client", None, raising=False)
    monkeypatch.setattr(lf_module, "_client_initialized", False, raising=False)


def test_trace_passthrough_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    _blank_langfuse(monkeypatch)

    @lf_module.trace("my-op")
    async def do_work(x: int) -> int:
        return x * 2

    result = asyncio.run(do_work(3))
    assert result == 6


def test_trace_propagates_original_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    _blank_langfuse(monkeypatch)

    @lf_module.trace("boom")
    async def blow_up() -> None:
        raise ValueError("original-message")

    with pytest.raises(ValueError, match="original-message"):
        asyncio.run(blow_up())


def test_trace_swallows_langfuse_client_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenClient:
        def trace(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("langfuse exploded")

    monkeypatch.setattr(lf_module, "_client", BrokenClient(), raising=False)
    monkeypatch.setattr(lf_module, "_client_initialized", True, raising=False)

    @lf_module.trace("x")
    async def work() -> str:
        return "ok"

    assert asyncio.run(work()) == "ok"


def test_trace_event_swallows_when_client_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _blank_langfuse(monkeypatch)
    # must not raise
    lf_module.trace_event("something", foo="bar")


def test_trace_handles_constructor_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "langfuse_public_key", "pk", raising=False)
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk", raising=False)
    monkeypatch.setattr(settings, "langfuse_base_url", "https://example", raising=False)
    monkeypatch.setattr(lf_module, "_client", None, raising=False)
    monkeypatch.setattr(lf_module, "_client_initialized", False, raising=False)

    class Boom:
        def __init__(self, *a: object, **kw: object) -> None:
            raise RuntimeError("ctor boom")

    monkeypatch.setattr(lf_module, "Langfuse", Boom, raising=False)

    @lf_module.trace("x")
    async def work() -> int:
        return 42

    assert asyncio.run(work()) == 42
