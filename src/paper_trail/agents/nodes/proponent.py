"""Proponent node — argues the claim is TRUE."""

from __future__ import annotations

from typing import Any

from paper_trail.agents.state import DebateState
from paper_trail.core.llm import chat
from paper_trail.core.prompts import load


async def proponent(state: DebateState) -> dict[str, Any]:
    """Generate a proponent argument for the current round."""
    system = load("proponent")
    claim = state.get("claim", "")
    plan = state.get("plan") or {}
    rnum = state.get("round", 0) + 1
    user = (
        f"Claim: {claim}\n"
        f"Plan: {plan}\n"
        f"Prior rounds: {state.get('rounds', [])}\n"
        f"Argue that the claim is TRUE for round {rnum}."
    )
    argument = await chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )
    entry: dict[str, Any] = {
        "side": "proponent",
        "round": rnum,
        "argument": argument,
        "evidence": list(plan.get("evidence", [])) if isinstance(plan, dict) else [],
    }
    return {"rounds": [entry]}
