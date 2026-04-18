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

from paper_trail.agents.tools.transcript import hash_transcript
from paper_trail.api.deps import get_service
from paper_trail.models.debate import Debate
from paper_trail.schemas.debates import (
    Citation,
    DebateCreateIn,
    DebateCreateOut,
    DebateListOut,
    DebateOut,
    TranscriptJsonOut,
    TranscriptRound,
    coerce_verdict,
)
from paper_trail.services.debates import DebateService

router = APIRouter(prefix="/debates", tags=["debates"])

# Tunables (monkeypatched in tests).
STREAM_POLL_SECONDS: float = 0.25
STREAM_MAX_SECONDS: float = 120.0
# SSE keepalive: emit a comment line every N seconds so proxies don't
# drop an idle stream while the LLM is thinking.
STREAM_KEEPALIVE_SECONDS: float = 10.0


def _to_debate_out(d: Debate) -> DebateOut:
    return DebateOut(
        id=d.id,
        claim=d.claim,
        status=d.status,
        verdict=coerce_verdict(d.verdict),
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
    # Block 6 (Spec 08): serialize the pool to JSON-native dicts so the
    # repository and graph layer can treat it as plain data (no Pydantic
    # entanglement past the HTTP boundary).
    pool_dicts: list[dict[str, Any]] | None = None
    if body.evidence_pool:
        pool_dicts = [
            {
                "certificate_id": str(item.certificate_id),
                "url": item.url,
                "title": item.title,
                "text": item.text,
            }
            for item in body.evidence_pool
        ]
    debate_id = await service.create(
        body.claim,
        body.max_rounds,
        evidence_pool=pool_dicts,
    )
    background.add_task(_run_debate_background, debate_id)
    return DebateCreateOut(
        debate_id=debate_id,
        stream_url=f"/debates/{debate_id}/stream",
    )


async def _run_debate_background(debate_id: UUID) -> None:
    """Run the debate graph in a fresh session (not the request-scoped one)."""
    from paper_trail.core.db import session_scope
    from paper_trail.repositories.debates import DebateRepo

    async with session_scope() as session:
        repo = DebateRepo(session)
        svc = DebateService(repo)
        await svc.run(debate_id)


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


def _coerce_rounds_struct(
    rounds_struct: list[dict[str, Any]] | None,
    fallback_rounds: list[dict[str, Any]],
) -> list[TranscriptRound]:
    """Coerce persisted rounds_struct (or fallback rounds) into typed rounds.

    Debates created before the render node started emitting `rounds_struct`
    still need to produce a valid transcript.json, so we fall back to
    adapting the raw `rounds` list.
    """
    src: list[dict[str, Any]]
    src = rounds_struct if rounds_struct is not None else fallback_rounds
    out: list[TranscriptRound] = []
    for entry in src:
        if not isinstance(entry, dict):
            continue
        citations_raw = entry.get("citations") or []
        citations: list[Citation] = []
        for c in citations_raw:
            if not isinstance(c, dict):
                continue
            ctype = c.get("type")
            ref = c.get("ref")
            if ctype not in ("cert", "url") or not ref:
                continue
            citations.append(
                Citation(
                    type=ctype,
                    ref=str(ref),
                    title=str(c.get("title") or ""),
                )
            )
        side_raw = str(entry.get("side", "")).lower()
        side: Any = side_raw if side_raw in ("proponent", "skeptic", "judge") else "proponent"
        argument_md = entry.get("argument_md")
        if not isinstance(argument_md, str):
            argument_md = str(entry.get("argument", ""))
        rnum_raw = entry.get("round", 0)
        try:
            rnum = int(rnum_raw)
        except (TypeError, ValueError):
            rnum = 0
        if rnum < 1:
            rnum = 1
        out.append(
            TranscriptRound(
                side=side,
                round=rnum,
                argument_md=argument_md,
                citations=citations,
            )
        )
    return out


@router.get("/{debate_id}/transcript.json", response_model=TranscriptJsonOut)
async def get_transcript_json(
    debate_id: UUID,
    service: Annotated[DebateService, Depends(get_service)],
) -> TranscriptJsonOut:
    d = await service.get(debate_id)
    if d is None:
        raise HTTPException(status_code=404, detail="debate not found")
    # Only completed debates have a transcript. Anything else (pending,
    # running, error) is not ready yet — signal 409 so clients know to
    # retry instead of treating it as a hard miss.
    if d.status != "done":
        raise HTTPException(status_code=409, detail="Debate still running")

    verdict = coerce_verdict(d.verdict)
    if verdict is None:
        # A `done` debate without a verdict is an internal invariant
        # violation. Raise 500 so it shows up loudly in monitoring rather
        # than silently serving a junk transcript.
        raise HTTPException(status_code=500, detail="debate complete but verdict missing")
    confidence = float(d.confidence if d.confidence is not None else 0.0)
    rounds_struct = d.rounds_struct if isinstance(d.rounds_struct, list) else None
    fallback_rounds = list(d.rounds or [])
    typed_rounds = _coerce_rounds_struct(rounds_struct, fallback_rounds)

    # Prefer the hash the render node already computed (same deterministic
    # canonicalization). For older debates missing it, compute it from the
    # coerced rounds — still deterministic, same inputs always → same hash.
    if d.transcript_hash:
        transcript_hash = d.transcript_hash
    else:
        rounds_payload = [r.model_dump() for r in typed_rounds]
        transcript_hash = hash_transcript(
            claim=d.claim,
            verdict=verdict,
            confidence=confidence,
            rounds=rounds_payload,
        )

    return TranscriptJsonOut(
        debate_id=d.id,
        claim=d.claim,
        verdict=verdict,
        confidence=confidence,
        rounds=typed_rounds,
        transcript_hash=transcript_hash,
    )


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
