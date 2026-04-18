"""HTTP DTOs for debate endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Verdict = Literal["TRUE", "FALSE", "INCONCLUSIVE"]
_VERDICT_VALUES: frozenset[str] = frozenset({"TRUE", "FALSE", "INCONCLUSIVE"})

# Block 6 (Spec 08) — caller-supplied evidence pool. Keep a generous but
# bounded ceiling: 50 items is the cap a well-behaved client should respect.
MAX_EVIDENCE_POOL_ITEMS = 50

# Citation types surfaced in the structured transcript. `cert` refers to
# an inkprint certificate id that was provided via the evidence pool;
# `url` refers to a Tavily-sourced external URL.
CitationType = Literal["cert", "url"]


def coerce_verdict(value: str | None) -> Verdict | None:
    """Narrow a DB-layer string into the Verdict literal.

    Until the DB column is migrated to a SQL enum (Phase 3), the model
    stores `verdict` as a plain string. This helper enforces the Literal
    contract at the HTTP boundary — parse, don't validate.
    """
    if value is None:
        return None
    if value not in _VERDICT_VALUES:
        raise ValueError(f"invalid verdict: {value!r}")
    return value  # type: ignore[return-value]


class EvidencePoolItem(BaseModel):
    """A single caller-supplied evidence record.

    Frozen + `extra=forbid` — parse, don't validate. Once accepted, the
    graph treats the `certificate_id` as the canonical citation ref.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    certificate_id: UUID
    url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)


class DebateCreateIn(BaseModel):
    claim: str = Field(min_length=1, max_length=2000)
    max_rounds: int = Field(default=5, ge=1, le=10)
    evidence_pool: list[EvidencePoolItem] | None = Field(
        default=None,
        max_length=MAX_EVIDENCE_POOL_ITEMS,
    )


class DebateCreateOut(BaseModel):
    debate_id: UUID
    stream_url: str


class Citation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: CitationType
    ref: str = Field(min_length=1)
    title: str = ""


class TranscriptRound(BaseModel):
    model_config = ConfigDict(extra="forbid")

    side: Literal["proponent", "skeptic", "judge"]
    round: int = Field(ge=1)
    argument_md: str = ""
    citations: list[Citation] = Field(default_factory=list)
    confidence: float | None = None


class TranscriptJsonOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    debate_id: UUID
    claim: str
    verdict: Verdict
    confidence: float
    rounds: list[TranscriptRound]
    transcript_hash: str = Field(min_length=64, max_length=64)


class DebateOut(BaseModel):
    id: UUID
    claim: str
    status: str
    verdict: Verdict | None
    confidence: float | None
    rounds: list[dict[str, Any]]
    transcript_md: str | None
    created_at: datetime


class DebateListOut(BaseModel):
    items: list[DebateOut]
    next_cursor: str | None


class PlatformDebateIn(BaseModel):
    claim: str = Field(min_length=1, max_length=2000)
    max_rounds: int | None = Field(default=3, ge=1, le=10)


class PlatformDebateOut(BaseModel):
    debate_id: UUID
    transcript_url: str
    verdict: Verdict
    confidence: float
    rounds_run: int
