"""Judge node — scores confidence, emits verdict, decides convergence."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from paper_trail.agents.nodes._format import format_prior_rounds
from paper_trail.agents.state import CONFIDENCE_THRESHOLD, DebateState
from paper_trail.core.llm import chat_json
from paper_trail.core.prompts import load


class Verdict(BaseModel):
    verdict: Literal["TRUE", "FALSE", "INCONCLUSIVE"]
    confidence: float = Field(ge=0.0, le=1.0)


async def judge(state: DebateState) -> dict[str, Any]:
    """Score the debate, emit verdict + confidence, decide convergence."""
    system = load("judge")
    claim = state.get("claim", "")
    rounds = state.get("rounds", [])
    cur_round = int(state.get("round", 0))
    max_rounds = int(state.get("max_rounds", 5))

    rounds_md = format_prior_rounds(rounds) or "_No rounds yet._"
    user = (
        f"## Claim\n{claim}\n\n"
        f"## Debate so far\n{rounds_md}\n\n"
        "## Debate status\n"
        f"- Round just completed: {cur_round + 1} of {max_rounds}\n"
        f"- Remaining rounds if needed: {max(0, max_rounds - cur_round - 1)}\n\n"
        "## Your task\n"
        "Apply the verdict and confidence rubrics from your system prompt. "
        "Return strictly valid JSON with keys `verdict` and `confidence`."
    )
    result = await chat_json(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        Verdict,
    )
    verdict = result.verdict
    confidence = float(result.confidence)
    next_round = cur_round + 1
    need_more = confidence < CONFIDENCE_THRESHOLD and next_round < max_rounds
    return {
        "verdict": verdict,
        "confidence": confidence,
        "need_more": need_more,
        "round": next_round,
    }
