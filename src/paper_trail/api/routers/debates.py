"""Routes for debates."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from sse_starlette.sse import EventSourceResponse

from paper_trail.api.deps import get_service
from paper_trail.schemas.debates import (
    DebateCreateIn,
    DebateCreateOut,
    DebateListOut,
    DebateOut,
)
from paper_trail.services.debates import DebateService

router = APIRouter(prefix="/debates", tags=["debates"])

# Tunables (monkeypatched in tests).
STREAM_POLL_SECONDS: float = 0.25
STREAM_MAX_SECONDS: float = 120.0
# SSE keepalive: emit a comment line every N seconds so proxies don't
# drop an idle stream while the LLM is thinking.
STREAM_KEEPALIVE_SECONDS: float = 10.0


def _to_debate_out(d: Any) -> DebateOut:
    return DebateOut(
        id=d.id,
        claim=d.claim,
        status=d.status,
        verdict=d.verdict,
        confidence=d.confidence,
        rounds=list(d.rounds or []),
        transcript_md=d.transcript_md,
        created_at=d.created_at,
    )


@router.post(
    "",
    response_model=DebateCreateOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_debate(
    body: DebateCreateIn,
    background: BackgroundTasks,
    service: Annotated[DebateService, Depends(get_service)],
) -> DebateCreateOut:
    debate_id = await service.create(body.claim, body.max_rounds)
    background.add_task(service.run, debate_id)
    return DebateCreateOut(
        debate_id=debate_id,
        stream_url=f"/debates/{debate_id}/stream",
    )


@router.get("", response_model=DebateListOut)
async def list_debates(
    service: Annotated[DebateService, Depends(get_service)],
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=100),
) -> DebateListOut:
    items, next_cursor = await service.list(cursor, limit)
    return DebateListOut(
        items=[_to_debate_out(d) for d in items],
        next_cursor=next_cursor,
    )


@router.get("/{debate_id}", response_model=DebateOut)
async def get_debate(
    debate_id: UUID,
    service: Annotated[DebateService, Depends(get_service)],
) -> DebateOut:
    d = await service.get(debate_id)
    if d is None:
        raise HTTPException(status_code=404, detail="debate not found")
    return _to_debate_out(d)


@router.get("/{debate_id}/transcript.md")
async def get_transcript(
    debate_id: UUID,
    service: Annotated[DebateService, Depends(get_service)],
) -> PlainTextResponse:
    d = await service.get(debate_id)
    if d is None or not d.transcript_md:
        raise HTTPException(status_code=404, detail="transcript not available")
    return PlainTextResponse(content=d.transcript_md, media_type="text/markdown")


@router.get("/{debate_id}/stream")
async def stream_debate(
    debate_id: UUID,
    service: Annotated[DebateService, Depends(get_service)],
) -> EventSourceResponse:
    async def event_gen() -> Any:
        deadline = time.monotonic() + STREAM_MAX_SECONDS
        last_snapshot: tuple[Any, ...] | None = None
        last_emit = time.monotonic()
        terminal_status = {"done", "failed", "error"}
        while time.monotonic() < deadline:
            d = await service.get(debate_id)
            if d is None:
                yield {
                    "event": "error",
                    "data": json.dumps({"reason": "not_found"}),
                }
                return
            rounds_list = list(d.rounds or [])
            # Snapshot includes rounds count AND a cheap fingerprint of
            # the latest round text so the stream fires when an agent
            # finishes writing even if `rounds_count` didn't change.
            latest_len = len(rounds_list[-1].get("argument", "")) if rounds_list else 0
            snapshot = (
                d.status,
                d.verdict,
                d.confidence,
                len(rounds_list),
                latest_len,
            )
            if snapshot != last_snapshot:
                last_snapshot = snapshot
                last_emit = time.monotonic()
                yield {
                    "event": "state",
                    "data": json.dumps(
                        {
                            "type": "state",
                            "status": d.status,
                            "verdict": d.verdict,
                            "confidence": d.confidence,
                            "rounds_count": len(rounds_list),
                            # Inline the rounds so the client doesn't
                            # have to round-trip a GET after every tick.
                            "rounds": rounds_list,
                        }
                    ),
                }
            elif time.monotonic() - last_emit > STREAM_KEEPALIVE_SECONDS:
                # Emit an SSE comment to keep the connection warm.
                last_emit = time.monotonic()
                yield {"event": "ping", "data": json.dumps({"t": last_emit})}
            if d.status in terminal_status:
                yield {
                    "event": "done",
                    "data": json.dumps(
                        {
                            "type": "done",
                            "status": d.status,
                            "verdict": d.verdict,
                            "confidence": d.confidence,
                            "rounds_count": len(rounds_list),
                            "rounds": rounds_list,
                        }
                    ),
                }
                return
            await asyncio.sleep(STREAM_POLL_SECONDS)
        # Safety timeout: emit done with last known state.
        yield {
            "event": "done",
            "data": json.dumps({"type": "done", "reason": "timeout"}),
        }

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    }
    return EventSourceResponse(event_gen(), headers=headers)
