"""Proponent node — argues the claim is TRUE."""

from __future__ import annotations

from typing import Any

from paper_trail.agents.nodes._format import format_evidence, format_prior_rounds
from paper_trail.agents.state import DebateState
from paper_trail.core.langfuse import span, update_current_span
from paper_trail.core.llm import chat
from paper_trail.core.prompts import load


async def proponent(state: DebateState) -> dict[str, Any]:
    """Generate a proponent argument for the current round."""
    claim = state.get("claim", "")
    plan = state.get("plan") or {}
    rnum = state.get("round", 0) + 1
    evidence = plan.get("evidence", []) if isinstance(plan, dict) else []
    sub_questions = plan.get("sub_questions", []) if isinstance(plan, dict) else []

    async with span(
        "node.proponent",
        input={"claim": claim, "round": rnum, "evidence_count": len(evidence)},
    ):
        system = load("proponent")
        user_parts = [
            f"## Claim\n{claim}",
            f"## Current round\n{rnum}",
        ]
        if sub_questions:
            user_parts.append(
                "## Sub-questions from the plan\n" + "\n".join(f"- {q}" for q in sub_questions)
            )
        user_parts.append("## Evidence\n" + format_evidence(evidence))
        prior = format_prior_rounds(state.get("rounds", []))
        if prior:
            user_parts.append(f"## Prior rounds of this debate\n{prior}")
        user_parts.append(
            "## Your task\n"
            f"Argue that the claim is TRUE for round {rnum}, following the rules "
            "in your system prompt. Cite at least one specific source by title."
        )
        user = "\n\n".join(user_parts)

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
            "evidence": list(evidence),
        }
        update_current_span(
            output={
                "round": rnum,
                "argument_length": len(argument),
                "argument": argument,
            }
        )
        return {"rounds": [entry]}
