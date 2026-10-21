"""Platform integration router — single synchronous debate endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from paper_trail.api.deps import get_service, require_bastion
from paper_trail.core.config import settings
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


@router.post(
    "/debate",
    response_model=PlatformDebateOut,
    # Bastion-only (spec 10 §4): checked before the rate limiter counts the call.
    dependencies=[Depends(require_bastion), Depends(rate_limiter("platform"))],
)
async def platform_debate(
    body: PlatformDebateIn,
    service: Annotated[DebateService, Depends(get_service)],
) -> PlatformDebateOut:
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
