"""Platform integration router — single synchronous debate endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException

from paper_trail.api.deps import get_service
from paper_trail.core.platform_auth import verify_platform_token
from paper_trail.schemas.debates import (
    PlatformDebateIn,
    PlatformDebateOut,
    coerce_verdict,
)
from paper_trail.services.debates import DebateService

router = APIRouter(prefix="/platform", tags=["platform"])

PLATFORM_MAX_ROUNDS_CAP = 3


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="invalid platform token")
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(status_code=401, detail="invalid platform token")
    return parts[1].strip()


@router.post("/debate", response_model=PlatformDebateOut)
async def platform_debate(
    body: PlatformDebateIn,
    service: Annotated[DebateService, Depends(get_service)],
    authorization: Annotated[str | None, Header()] = None,
) -> PlatformDebateOut:
    token = _extract_bearer(authorization)
    if not verify_platform_token(token):
        raise HTTPException(status_code=401, detail="invalid platform token")

    requested = body.max_rounds or PLATFORM_MAX_ROUNDS_CAP
    effective_max_rounds = min(requested, PLATFORM_MAX_ROUNDS_CAP)

    debate_id = await service.create(body.claim, effective_max_rounds)
    await service.run(debate_id)
    debate = await service.get(debate_id)
    if debate is None:
        raise HTTPException(status_code=500, detail="debate disappeared after run")

    return PlatformDebateOut(
        debate_id=debate_id,
        transcript_url=f"/debates/{debate_id}/transcript.md",
        verdict=coerce_verdict(debate.verdict) or "INCONCLUSIVE",
        confidence=float(debate.confidence or 0.0),
        rounds_run=len(list(debate.rounds or [])),
    )
