"""Session setup shared by every tier.

The suite runs under the test settings (docs/specs/10-platform-contract-v2.md
§3): with no trusted key configured, a protected route passes as anonymous
instead of failing closed with 503. Set before any ``paper_trail`` import, so
the settings object sees it; an exported ``APP_ENV`` or a local ``.env`` cannot
switch the suite into fail-closed mode. Tests that need the production
behaviour set ``settings.app_env`` themselves.

Tracing is off for the same reason (docs/specs/12-tracing.md): blank Langfuse
keys beat whatever a local ``.env`` holds, so no test sends spans to Langfuse.
Tests that assert on traces take the ``traces`` fixture, a client on an
in-memory exporter.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

os.environ["APP_ENV"] = "test"
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""

from langfuse import Langfuse
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode

from paper_trail.core import tracing

_METADATA = "langfuse.observation.metadata."


@dataclass
class Traces:
    """The spans a test exported, and readers for the Langfuse attributes on them."""

    client: Langfuse
    exporter: InMemorySpanExporter

    def spans(self) -> list[ReadableSpan]:
        self.client.flush()
        return list(self.exporter.get_finished_spans())

    def named(self, name: str) -> list[ReadableSpan]:
        return sorted((s for s in self.spans() if s.name == name), key=lambda s: s.start_time or 0)

    def one(self, name: str) -> ReadableSpan:
        found = self.named(name)
        assert len(found) == 1, f"{len(found)} spans named {name!r}"
        return found[0]

    def parent(self, span: ReadableSpan) -> ReadableSpan | None:
        if span.parent is None:
            return None
        return next((s for s in self.spans() if s.context.span_id == span.parent.span_id), None)

    def children(self, span: ReadableSpan) -> list[ReadableSpan]:
        kids = [
            s
            for s in self.spans()
            if s.parent is not None and s.parent.span_id == span.context.span_id
        ]
        return sorted(kids, key=lambda s: s.start_time or 0)

    @staticmethod
    def attr(span: ReadableSpan, key: str) -> Any:
        return (span.attributes or {}).get(key)

    @staticmethod
    def decoded(span: ReadableSpan, key: str) -> Any:
        raw = (span.attributes or {}).get(key)
        return None if raw is None else json.loads(str(raw))

    @staticmethod
    def type_of(span: ReadableSpan) -> Any:
        return (span.attributes or {}).get("langfuse.observation.type")

    @staticmethod
    def level(span: ReadableSpan) -> str:
        """The level Langfuse derives: the explicit attribute, else ERROR on an error status."""
        explicit = (span.attributes or {}).get("langfuse.observation.level")
        if explicit is not None:
            return str(explicit)
        return "ERROR" if span.status.status_code is StatusCode.ERROR else "DEFAULT"

    @staticmethod
    def status_message(span: ReadableSpan) -> str | None:
        explicit = (span.attributes or {}).get("langfuse.observation.status_message")
        return str(explicit) if explicit is not None else span.status.description

    @staticmethod
    def metadata(span: ReadableSpan) -> dict[str, Any]:
        return {
            key.removeprefix(_METADATA): json.loads(str(value))
            for key, value in (span.attributes or {}).items()
            if key.startswith(_METADATA)
        }


def tracing_client(exporter: SpanExporter) -> Langfuse:
    """A Langfuse client that exports to ``exporter`` only, never to the network.

    A fresh public key per client: the SDK keeps one set of resources per key.
    """
    return Langfuse(
        public_key=f"pk-lf-test-{uuid4().hex}",
        secret_key="sk-lf-test",
        base_url="http://langfuse.invalid",
        tracer_provider=TracerProvider(),
        span_exporter=exporter,
        environment="test",
    )


@pytest.fixture
def traces(monkeypatch: pytest.MonkeyPatch) -> Iterator[Traces]:
    exporter = InMemorySpanExporter()
    client = tracing_client(exporter)
    monkeypatch.setattr(tracing, "_client", client)
    yield Traces(client=client, exporter=exporter)
    client.shutdown()
