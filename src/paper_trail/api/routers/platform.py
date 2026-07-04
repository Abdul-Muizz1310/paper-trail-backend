"""Platform integration router — single synchronous debate endpoint."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException

from paper_trail.api.deps import get_service
from paper_trail.core.config import settings
from paper_trail.core.platform_auth import verify_platform_token
from paper_trail.core.rate_limit import rate_limiter
from paper_trail.core.signing import SIGNATURE_ALG, public_key_pem
from paper_trail.schemas.debates import (
    PlatformDebateIn,
    PlatformDebateOut,
    coerce_verdict,
)
from paper_trail.services.debates import DebateService

router = APIRouter(prefix="/platform", tags=["platform"])

PLATFORM_MAX_ROUNDS_CAP = 3


@router.get("/receipt-public-key")
async def receipt_public_key() -> dict[str, str]:
    """Publish the Ed25519 public key used to sign transcript receipts.

    Third parties verify a `transcript.json` signature against this key. Returns
    404 when no signing key is configured (receipts are unsigned).
    """
    pem = public_key_pem()
    if pem is None:
        raise HTTPException(status_code=404, detail="transcript signing not configured")
    return {"alg": SIGNATURE_ALG, "public_key": pem}


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="invalid platform token")
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(status_code=401, detail="invalid platform token")
    return parts[1].strip()


@router.post(
    "/debate",
    response_model=PlatformDebateOut,
    dependencies=[Depends(rate_limiter("platform"))],
)
async def platform_debate(
    body: PlatformDebateIn,
    service: Annotated[DebateService, Depends(get_service)],
    authorization: Annotated[str | None, Header()] = None,
) -> PlatformDebateOut:
    token = _extract_bearer(authorization)
    # Verification may touch the network (URL-based bastion key on a cold
    # cache); run it off the event loop so it can't stall other requests.
    if not await asyncio.to_thread(verify_platform_token, token):
        raise HTTPException(status_code=401, detail="invalid platform token")

    requested = body.max_rounds or PLATFORM_MAX_ROUNDS_CAP
    effective_max_rounds = min(requested, PLATFORM_MAX_ROUNDS_CAP)

    debate_id = await service.create(body.claim, effective_max_rounds)
    await service.run(debate_id)
    debate = await service.get(debate_id)
    if debate is None:
        raise HTTPException(status_code=500, detail="debate disappeared after run")

    # Absolute URL per spec 99 (reads PUBLIC_BASE_URL).
    base = settings.public_base_url.rstrip("/")
    return PlatformDebateOut(
        debate_id=debate_id,
        transcript_url=f"{base}/debates/{debate_id}/transcript.md",
        verdict=coerce_verdict(debate.verdict) or "INCONCLUSIVE",
        confidence=float(debate.confidence or 0.0),
        rounds_run=len(list(debate.rounds or [])),
    )
