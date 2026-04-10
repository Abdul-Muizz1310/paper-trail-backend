"""Debate SQLAlchemy model."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, Float, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class DebateStatus(enum.StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    error = "error"


class DebateVerdict(enum.StrEnum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    INCONCLUSIVE = "INCONCLUSIVE"


class Base(DeclarativeBase):
    pass


class Debate(Base):
    __tablename__ = "debates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    max_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    status: Mapped[str] = mapped_column(
        Enum(DebateStatus, native_enum=False, length=20),
        nullable=False,
        default=DebateStatus.pending,
    )
    verdict: Mapped[str | None] = mapped_column(
        Enum(DebateVerdict, native_enum=False, length=20),
        nullable=True,
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    rounds: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    transcript_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
