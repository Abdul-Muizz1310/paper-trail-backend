"""Async SQLAlchemy repository for debates."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from paper_trail.models.debate import Debate, DebateStatus


def _encode_cursor(created_at: datetime, did: UUID) -> str:
    raw = f"{created_at.isoformat()}|{did}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    ts, did = raw.split("|", 1)
    return datetime.fromisoformat(ts), UUID(did)


class DebateRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        claim: str,
        max_rounds: int,
        evidence_pool: list[dict[str, Any]] | None = None,
    ) -> Debate:
        # Normalize an empty pool to NULL so "absent" and "empty" are
        # indistinguishable at the storage layer.
        normalized_pool = evidence_pool if evidence_pool else None
        d = Debate(
            claim=claim,
            max_rounds=max_rounds,
            status=DebateStatus.pending,
            rounds=[],
            evidence_pool=normalized_pool,
        )
        self.session.add(d)
        await self.session.flush()
        await self.session.refresh(d)
        return d

    async def get(self, debate_id: UUID) -> Debate | None:
        result = await self.session.execute(select(Debate).where(Debate.id == debate_id))
        return result.scalar_one_or_none()

    async def list_page(
        self, cursor: str | None, limit: int = 50
    ) -> tuple[list[Debate], str | None]:
        stmt = select(Debate).order_by(desc(Debate.created_at), desc(Debate.id))
        if cursor:
            ts, cursor_id = _decode_cursor(cursor)
            stmt = stmt.where(
                (Debate.created_at < ts) | ((Debate.created_at == ts) & (Debate.id < cursor_id))
            )
        stmt = stmt.limit(limit + 1)
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())
        next_cursor: str | None = None
        if len(rows) > limit:
            rows = rows[:limit]
            last = rows[-1]
            next_cursor = _encode_cursor(last.created_at, last.id)
        return rows, next_cursor

    async def update_result(
        self,
        debate_id: UUID,
        verdict: str,
        confidence: float,
        rounds: list[dict[str, Any]],
        transcript_md: str,
        rounds_struct: list[dict[str, Any]] | None = None,
        transcript_hash: str | None = None,
    ) -> None:
        d = await self.get(debate_id)
        if d is None:
            raise ValueError(f"debate {debate_id} not found")
        d.verdict = verdict
        d.confidence = confidence
        d.rounds = rounds
        d.transcript_md = transcript_md
        # Block 6 (Spec 08) — structured transcript artifacts, both
        # nullable when the graph hasn't produced them yet (older code
        # paths, tests).
        if rounds_struct is not None:
            d.rounds_struct = rounds_struct
        if transcript_hash is not None:
            d.transcript_hash = transcript_hash
        d.status = DebateStatus.done
        d.updated_at = datetime.now(UTC)

    async def set_status(self, debate_id: UUID, status: str) -> None:
        d = await self.get(debate_id)
        if d is None:
            raise ValueError(f"debate {debate_id} not found")
        d.status = status
        d.updated_at = datetime.now(UTC)

    async def update_judge_progress(
        self,
        debate_id: UUID,
        verdict: str | None,
        confidence: float | None,
    ) -> None:
        """Persist interim verdict/confidence after each judge pass."""
        d = await self.get(debate_id)
        if d is None:
            raise ValueError(f"debate {debate_id} not found")
        if verdict is not None:
            d.verdict = verdict
        if confidence is not None:
            d.confidence = float(confidence)
        d.updated_at = datetime.now(UTC)

    async def update_rounds(self, debate_id: UUID, rounds: list[dict[str, Any]]) -> None:
        """Incrementally persist round progress while the graph runs.

        This lets SSE consumers see rounds appear one at a time instead
        of all at once at the end of the debate.
        """
        d = await self.get(debate_id)
        if d is None:
            raise ValueError(f"debate {debate_id} not found")
        d.rounds = rounds
        d.updated_at = datetime.now(UTC)
