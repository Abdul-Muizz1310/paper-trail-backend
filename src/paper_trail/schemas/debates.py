"""HTTP DTOs for debate endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

Verdict = Literal["TRUE", "FALSE", "INCONCLUSIVE"]


class DebateCreateIn(BaseModel):
    claim: str = Field(min_length=1, max_length=2000)
    max_rounds: int = Field(default=5, ge=1, le=10)


class DebateCreateOut(BaseModel):
    debate_id: UUID
    stream_url: str


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
