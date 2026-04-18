"""Debate state — LangGraph TypedDict with append reducer on rounds."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

Verdict = Literal["TRUE", "FALSE", "INCONCLUSIVE"]


class RoundEntry(TypedDict, total=False):
    side: Literal["proponent", "skeptic"]
    round: int
    argument: str
    evidence: list[dict[str, Any]]
    # Block 6 (Spec 08): typed citations resolved from the evidence pool
    # and Tavily hits. Optional so existing states without the field remain
    # valid.
    citations: list[dict[str, Any]]


class DebateState(TypedDict, total=False):
    claim: str
    max_rounds: int
    round: int
    rounds: Annotated[list[RoundEntry], operator.add]
    plan: dict[str, Any] | None
    verdict: Verdict | None
    confidence: float | None
    reasoning: str | None
    need_more: bool
    transcript_md: str | None
    # Block 6 (Spec 08): caller-supplied evidence pool (frozen dicts with
    # certificate_id / url / title / text) and the structured rounds +
    # hash that the render node produces.
    evidence_pool: list[dict[str, Any]] | None
    rounds_struct: list[dict[str, Any]] | None
    transcript_hash: str | None


CONFIDENCE_THRESHOLD = 0.85
MAX_CLAIM_LEN = 2000


def initial_state(
    claim: str,
    max_rounds: int,
    evidence_pool: list[dict[str, Any]] | None = None,
) -> DebateState:
    if not claim or not claim.strip():
        raise ValueError("claim must be non-empty")
    if len(claim) > MAX_CLAIM_LEN:
        raise ValueError(f"claim must be <= {MAX_CLAIM_LEN} chars")
    if max_rounds < 1:
        raise ValueError("max_rounds must be >=1")
    # Normalize an empty pool to None — "absent" and "empty list" are
    # equivalent at the graph layer (parse, don't validate).
    normalized_pool = evidence_pool if evidence_pool else None
    return DebateState(
        claim=claim,
        max_rounds=max_rounds,
        round=0,
        rounds=[],
        plan=None,
        verdict=None,
        confidence=None,
        need_more=True,
        transcript_md=None,
        evidence_pool=normalized_pool,
        rounds_struct=None,
        transcript_hash=None,
    )


def is_converged(state: DebateState) -> bool:
    conf = state.get("confidence")
    rd = state.get("round", 0)
    mx = state.get("max_rounds", 5)
    if rd >= mx:
        return True
    return conf is not None and conf >= CONFIDENCE_THRESHOLD


def validate_state(state: DebateState) -> None:
    conf = state.get("confidence")
    if conf is not None and not (0.0 <= conf <= 1.0):
        raise ValueError("confidence must be in [0,1]")
    verdict = state.get("verdict")
    if verdict is not None and verdict not in ("TRUE", "FALSE", "INCONCLUSIVE"):
        raise ValueError(f"invalid verdict: {verdict}")
