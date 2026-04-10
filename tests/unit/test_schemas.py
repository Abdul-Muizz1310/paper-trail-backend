"""Unit tests for schemas/debates.py."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from paper_trail.schemas.debates import (
    DebateCreateIn,
    DebateOut,
    PlatformDebateIn,
)


class TestDebateCreateIn:
    def test_defaults(self) -> None:
        m = DebateCreateIn(claim="x")
        assert m.max_rounds == 5

    def test_too_long(self) -> None:
        with pytest.raises(ValidationError):
            DebateCreateIn(claim="x" * 2001)

    def test_empty(self) -> None:
        with pytest.raises(ValidationError):
            DebateCreateIn(claim="")

    def test_max_rounds_clamped(self) -> None:
        with pytest.raises(ValidationError):
            DebateCreateIn(claim="x", max_rounds=0)
        with pytest.raises(ValidationError):
            DebateCreateIn(claim="x", max_rounds=11)


class TestPlatformDebateIn:
    def test_defaults(self) -> None:
        m = PlatformDebateIn(claim="x")
        assert m.max_rounds == 3

    def test_max_rounds_optional(self) -> None:
        m = PlatformDebateIn(claim="x", max_rounds=2)
        assert m.max_rounds == 2


def test_debate_out_roundtrip() -> None:
    did = uuid4()
    now = datetime.utcnow()
    m = DebateOut(
        id=did,
        claim="x",
        status="done",
        verdict="TRUE",
        confidence=0.9,
        rounds=[{"side": "proponent", "round": 1, "argument": "a", "evidence": []}],
        transcript_md="# Title",
        created_at=now,
    )
    data = m.model_dump()
    m2 = DebateOut(**data)
    assert m2.id == did
    assert m2.verdict == "TRUE"


# ---------------------------------------------------------------------------
# coerce_verdict
# ---------------------------------------------------------------------------

from paper_trail.schemas.debates import coerce_verdict


def test_coerce_verdict_valid_values() -> None:
    assert coerce_verdict("TRUE") == "TRUE"
    assert coerce_verdict("FALSE") == "FALSE"
    assert coerce_verdict("INCONCLUSIVE") == "INCONCLUSIVE"


def test_coerce_verdict_none_returns_none() -> None:
    assert coerce_verdict(None) is None


def test_coerce_verdict_invalid_raises_value_error() -> None:
    with pytest.raises(ValueError, match="invalid verdict"):
        coerce_verdict("MAYBE")
