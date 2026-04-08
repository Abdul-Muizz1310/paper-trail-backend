"""Debate state — LangGraph TypedDict with append reducer on rounds."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

Verdict = Literal["TRUE", "FALSE", "INCONCLUSIVE"]


class RoundEntry(TypedDict):
    side: Literal["proponent", "skeptic"]
    round: int
    argument: str
    evidence: list[dict[str, Any]]


class DebateState(TypedDict, total=False):
    claim: str
    max_rounds: int
    round: int
    rounds: Annotated[list[RoundEntry], operator.add]
    plan: dict[str, Any] | None
    verdict: Verdict | None
    confidence: float | None
    need_more: bool
    transcript_md: str | None


CONFIDENCE_THRESHOLD = 0.85
MAX_CLAIM_LEN = 2000


def initial_state(claim: str, max_rounds: int) -> DebateState:
    if not claim or not claim.strip():
        raise ValueError("claim must be non-empty")
    if len(claim) > MAX_CLAIM_LEN:
        raise ValueError(f"claim must be <= {MAX_CLAIM_LEN} chars")
    if max_rounds < 1:
        raise ValueError("max_rounds must be >=1")
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
