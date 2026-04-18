"""Unit tests for schemas/debates.py."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from paper_trail.schemas.debates import (
    Citation,
    DebateCreateIn,
    DebateOut,
    EvidencePoolItem,
    PlatformDebateIn,
    TranscriptJsonOut,
    TranscriptRound,
    coerce_verdict,
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


def test_coerce_verdict_valid_values() -> None:
    assert coerce_verdict("TRUE") == "TRUE"
    assert coerce_verdict("FALSE") == "FALSE"
    assert coerce_verdict("INCONCLUSIVE") == "INCONCLUSIVE"


def test_coerce_verdict_none_returns_none() -> None:
    assert coerce_verdict(None) is None


def test_coerce_verdict_invalid_raises_value_error() -> None:
    with pytest.raises(ValueError, match="invalid verdict"):
        coerce_verdict("MAYBE")


# ---------------------------------------------------------------------------
# Block 6 (Spec 08) — evidence pool + structured transcript
# ---------------------------------------------------------------------------


class TestEvidencePoolItem:
    def test_valid(self) -> None:
        cid = uuid4()
        item = EvidencePoolItem(
            certificate_id=cid,
            url="https://example.com",
            title="T",
            text="body",
        )
        assert item.certificate_id == cid
        assert item.url == "https://example.com"

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            EvidencePoolItem(  # type: ignore[call-arg]
                certificate_id=uuid4(),
                url="u",
                title="t",
                text="body",
                extra="nope",
            )

    def test_missing_text_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvidencePoolItem(  # type: ignore[call-arg]
                certificate_id=uuid4(),
                url="u",
                title="t",
            )

    def test_empty_text_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvidencePoolItem(certificate_id=uuid4(), url="u", title="t", text="")

    def test_non_uuid_certificate_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvidencePoolItem(
                certificate_id="not-a-uuid",  # type: ignore[arg-type]
                url="u",
                title="t",
                text="x",
            )


class TestDebateCreateInEvidencePool:
    def test_pool_absent_is_none(self) -> None:
        m = DebateCreateIn(claim="x")
        assert m.evidence_pool is None

    def test_pool_empty_list_allowed(self) -> None:
        m = DebateCreateIn(claim="x", evidence_pool=[])
        assert m.evidence_pool == []

    def test_pool_up_to_50_items_allowed(self) -> None:
        items = [
            EvidencePoolItem(
                certificate_id=uuid4(),
                url=f"https://x{i}",
                title=f"t{i}",
                text="body",
            )
            for i in range(50)
        ]
        m = DebateCreateIn(claim="x", evidence_pool=items)
        assert m.evidence_pool is not None
        assert len(m.evidence_pool) == 50

    def test_pool_51_items_rejected(self) -> None:
        items = [
            EvidencePoolItem(
                certificate_id=uuid4(),
                url=f"https://x{i}",
                title=f"t{i}",
                text="body",
            )
            for i in range(51)
        ]
        with pytest.raises(ValidationError):
            DebateCreateIn(claim="x", evidence_pool=items)


def test_citation_shape() -> None:
    c = Citation(type="cert", ref="abc", title="T")
    assert c.type == "cert"
    assert c.ref == "abc"


def test_transcript_round_defaults() -> None:
    r = TranscriptRound(side="proponent", round=1, argument_md="md")
    assert r.citations == []
    assert r.confidence is None


def test_transcript_round_rejects_round_zero() -> None:
    with pytest.raises(ValidationError):
        TranscriptRound(side="proponent", round=0, argument_md="md")


def test_transcript_json_out_shape() -> None:
    m = TranscriptJsonOut(
        debate_id=uuid4(),
        claim="c",
        verdict="TRUE",
        confidence=0.9,
        rounds=[],
        transcript_hash="a" * 64,
    )
    assert len(m.transcript_hash) == 64


def test_transcript_json_out_rejects_short_hash() -> None:
    with pytest.raises(ValidationError):
        TranscriptJsonOut(
            debate_id=uuid4(),
            claim="c",
            verdict="TRUE",
            confidence=0.9,
            rounds=[],
            transcript_hash="short",
        )
