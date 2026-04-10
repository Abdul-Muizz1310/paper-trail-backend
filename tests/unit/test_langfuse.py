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
        def start_as_current_observation(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("langfuse exploded")

        def flush(self) -> None:
            return None

    monkeypatch.setattr(lf_module, "_client", BrokenClient(), raising=False)
    monkeypatch.setattr(lf_module, "_client_initialized", True, raising=False)

    @lf_module.trace("x")
    async def work() -> str:
        return "ok"

    assert asyncio.run(work()) == "ok"


def test_trace_swallows_span_exit_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class BadSpan:
        def __enter__(self) -> object:
            return self

        def __exit__(self, *a: object) -> None:
            raise RuntimeError("exit boom")

    class FlakyClient:
        def start_as_current_observation(self, **kwargs: object) -> object:
            return BadSpan()

        def flush(self) -> None:
            raise RuntimeError("flush boom")

    monkeypatch.setattr(lf_module, "_client", FlakyClient(), raising=False)
    monkeypatch.setattr(lf_module, "_client_initialized", True, raising=False)

    @lf_module.trace("x")
    async def work() -> int:
        return 7

    assert asyncio.run(work()) == 7


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


# ---------------------------------------------------------------------------
# _json_dump fallback
# ---------------------------------------------------------------------------


def test_json_dump_primitive() -> None:
    assert lf_module._json_dump({"a": 1}) == '{"a": 1}'


def test_json_dump_falls_back_to_str_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: object, **_kw: object) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(lf_module.json, "dumps", _raise)

    class Obj:
        def __str__(self) -> str:
            return "stringified"

    assert lf_module._json_dump(Obj()) == "stringified"


# ---------------------------------------------------------------------------
# span — input + metadata forwarding, BaseException propagation
# ---------------------------------------------------------------------------


def test_span_forwards_input_and_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    received: dict[str, object] = {}

    class Ctx:
        def __enter__(self) -> object:
            return "handle"

        def __exit__(self, *a: object) -> None:
            return None

    class Client:
        def start_as_current_observation(self, **kwargs: object) -> object:
            received.update(kwargs)
            return Ctx()

        def flush(self) -> None:
            return None

    monkeypatch.setattr(lf_module, "_client", Client(), raising=False)
    monkeypatch.setattr(lf_module, "_client_initialized", True, raising=False)

    async def run() -> None:
        async with lf_module.span(
            "op", input={"foo": "bar"}, metadata={"k": "v"}, as_type="generation"
        ) as handle:
            assert handle == "handle"

    asyncio.run(run())
    assert received["name"] == "op"
    assert received["input"] == {"foo": "bar"}
    assert received["metadata"] == {"k": "v"}
    assert received["as_type"] == "generation"


def test_span_exit_error_on_exception_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the body raises AND __exit__ also raises, original exception bubbles up."""

    class BadExitCtx:
        def __enter__(self) -> object:
            return self

        def __exit__(self, *a: object) -> None:
            raise RuntimeError("exit boom")

    class Client:
        def start_as_current_observation(self, **_kw: object) -> object:
            return BadExitCtx()

        def flush(self) -> None:
            raise RuntimeError("flush boom")

    monkeypatch.setattr(lf_module, "_client", Client(), raising=False)
    monkeypatch.setattr(lf_module, "_client_initialized", True, raising=False)

    async def run() -> None:
        async with lf_module.span("op"):
            raise ValueError("body boom")

    with pytest.raises(ValueError, match="body boom"):
        asyncio.run(run())


# ---------------------------------------------------------------------------
# update_current_span — all branches
# ---------------------------------------------------------------------------


def test_update_current_span_noop_when_client_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _blank_langfuse(monkeypatch)
    lf_module.update_current_span(output="x")  # must not raise


def test_update_current_span_noop_when_no_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"n": 0}

    class Client:
        def update_current_span(self, **_kw: object) -> None:
            called["n"] += 1

    monkeypatch.setattr(lf_module, "_client", Client(), raising=False)
    monkeypatch.setattr(lf_module, "_client_initialized", True, raising=False)

    lf_module.update_current_span()  # no output, no metadata
    assert called["n"] == 0


def test_update_current_span_forwards_output_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, object] = {}

    class Client:
        def update_current_span(self, **kw: object) -> None:
            received.update(kw)

    monkeypatch.setattr(lf_module, "_client", Client(), raising=False)
    monkeypatch.setattr(lf_module, "_client_initialized", True, raising=False)

    lf_module.update_current_span(output="result", metadata={"tokens": 42})
    assert received == {"output": "result", "metadata": {"tokens": 42}}


def test_update_current_span_swallows_client_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class Client:
        def update_current_span(self, **_kw: object) -> None:
            raise RuntimeError("boom")

    monkeypatch.setattr(lf_module, "_client", Client(), raising=False)
    monkeypatch.setattr(lf_module, "_client_initialized", True, raising=False)

    lf_module.update_current_span(output="x")  # must not raise


# ---------------------------------------------------------------------------
# update_current_trace — all branches
# ---------------------------------------------------------------------------


def test_update_current_trace_noop_when_client_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _blank_langfuse(monkeypatch)
    # must not raise
    lf_module.update_current_trace(name="x", tags=["a"], user_id="u")


def test_update_current_trace_noop_when_span_not_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSpan:
        def is_recording(self) -> bool:
            return False

        def set_attribute(self, *_a: object, **_kw: object) -> None:
            raise AssertionError("should not be called")

    class FakeOtel:
        @staticmethod
        def get_current_span() -> FakeSpan:
            return FakeSpan()

    class Client:
        pass

    monkeypatch.setattr(lf_module, "_client", Client(), raising=False)
    monkeypatch.setattr(lf_module, "_client_initialized", True, raising=False)
    monkeypatch.setattr(lf_module, "_otel_trace", FakeOtel, raising=False)
    # LangfuseOtelSpanAttributes must be non-None to pass the guard
    monkeypatch.setattr(
        lf_module,
        "LangfuseOtelSpanAttributes",
        type("Attrs", (), {}),
        raising=False,
    )

    lf_module.update_current_trace(name="x")  # must not raise


def test_update_current_trace_sets_all_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    set_attrs: dict[str, object] = {}

    class FakeSpan:
        def is_recording(self) -> bool:
            return True

        def set_attribute(self, key: str, value: object) -> None:
            set_attrs[key] = value

    class FakeOtel:
        @staticmethod
        def get_current_span() -> FakeSpan:
            return FakeSpan()

    class Attrs:
        TRACE_NAME = "langfuse.trace.name"
        TRACE_INPUT = "langfuse.trace.input"
        TRACE_OUTPUT = "langfuse.trace.output"
        TRACE_TAGS = "langfuse.trace.tags"
        TRACE_METADATA = "langfuse.trace.metadata"
        TRACE_USER_ID = "langfuse.trace.user_id"
        TRACE_SESSION_ID = "langfuse.trace.session_id"

    class Client:
        pass

    monkeypatch.setattr(lf_module, "_client", Client(), raising=False)
    monkeypatch.setattr(lf_module, "_client_initialized", True, raising=False)
    monkeypatch.setattr(lf_module, "_otel_trace", FakeOtel, raising=False)
    monkeypatch.setattr(lf_module, "LangfuseOtelSpanAttributes", Attrs, raising=False)

    lf_module.update_current_trace(
        name="trace-name",
        input={"a": 1},
        output={"b": 2},
        tags=["t1", "t2"],
        metadata={"k": "v"},
        user_id="user-42",
        session_id="session-7",
    )

    assert set_attrs["langfuse.trace.name"] == "trace-name"
    assert set_attrs["langfuse.trace.tags"] == ["t1", "t2"]
    assert set_attrs["langfuse.trace.user_id"] == "user-42"
    assert set_attrs["langfuse.trace.session_id"] == "session-7"
    assert "langfuse.trace.input" in set_attrs
    assert "langfuse.trace.output" in set_attrs
    assert "langfuse.trace.metadata" in set_attrs


def test_update_current_trace_swallows_set_attribute_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSpan:
        def is_recording(self) -> bool:
            return True

        def set_attribute(self, *_a: object, **_kw: object) -> None:
            raise RuntimeError("attr boom")

    class FakeOtel:
        @staticmethod
        def get_current_span() -> FakeSpan:
            return FakeSpan()

    class Attrs:
        TRACE_NAME = "langfuse.trace.name"
        TRACE_INPUT = "langfuse.trace.input"
        TRACE_OUTPUT = "langfuse.trace.output"
        TRACE_TAGS = "langfuse.trace.tags"
        TRACE_METADATA = "langfuse.trace.metadata"
        TRACE_USER_ID = "langfuse.trace.user_id"
        TRACE_SESSION_ID = "langfuse.trace.session_id"

    class Client:
        pass

    monkeypatch.setattr(lf_module, "_client", Client(), raising=False)
    monkeypatch.setattr(lf_module, "_client_initialized", True, raising=False)
    monkeypatch.setattr(lf_module, "_otel_trace", FakeOtel, raising=False)
    monkeypatch.setattr(lf_module, "LangfuseOtelSpanAttributes", Attrs, raising=False)

    # must not raise
    lf_module.update_current_trace(name="x")


# ---------------------------------------------------------------------------
# trace_event — exception branch
# ---------------------------------------------------------------------------


def test_trace_event_swallows_create_event_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class Client:
        def create_event(self, **_kw: object) -> None:
            raise RuntimeError("event boom")

    monkeypatch.setattr(lf_module, "_client", Client(), raising=False)
    monkeypatch.setattr(lf_module, "_client_initialized", True, raising=False)

    # must not raise
    lf_module.trace_event("something", foo="bar")


def test_trace_event_calls_create_event_on_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class Client:
        def create_event(self, **kw: object) -> None:
            captured.update(kw)

    monkeypatch.setattr(lf_module, "_client", Client(), raising=False)
    monkeypatch.setattr(lf_module, "_client_initialized", True, raising=False)

    lf_module.trace_event("my-event", a=1, b="two")
    assert captured["name"] == "my-event"
    assert captured["metadata"] == {"a": 1, "b": "two"}
