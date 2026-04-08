"""Async SQLAlchemy repository for debates."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from paper_trail.models.debate import Debate


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

    async def create(self, claim: str, max_rounds: int) -> Debate:
        d = Debate(claim=claim, max_rounds=max_rounds, status="pending", rounds=[])
        self.session.add(d)
        await self.session.flush()
        await self.session.commit()
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
            ts, _did = _decode_cursor(cursor)
            stmt = stmt.where(Debate.created_at < ts)
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
    ) -> None:
        d = await self.get(debate_id)
        if d is None:
            raise ValueError(f"debate {debate_id} not found")
        d.verdict = verdict
        d.confidence = confidence
        d.rounds = rounds
        d.transcript_md = transcript_md
        d.status = "done"
        d.updated_at = datetime.utcnow()
        await self.session.commit()

    async def set_status(self, debate_id: UUID, status: str) -> None:
        d = await self.get(debate_id)
        if d is None:
            raise ValueError(f"debate {debate_id} not found")
        d.status = status
        d.updated_at = datetime.utcnow()
        await self.session.commit()
