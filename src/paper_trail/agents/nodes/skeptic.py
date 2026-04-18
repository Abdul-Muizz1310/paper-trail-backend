"""Skeptic node — argues the claim is FALSE."""

from __future__ import annotations

from typing import Any

from paper_trail.agents.nodes._citations import build_round_citations
from paper_trail.agents.nodes._format import (
    format_evidence,
    format_evidence_pool,
    format_prior_rounds,
)
from paper_trail.agents.state import DebateState
from paper_trail.core.langfuse import span, update_current_span
from paper_trail.core.llm import chat
from paper_trail.core.prompts import load


async def skeptic(state: DebateState) -> dict[str, Any]:
    """Generate a skeptic argument for the current round."""
    claim = state.get("claim", "")
    plan = state.get("plan") or {}
    rnum = state.get("round", 0) + 1
    evidence = plan.get("evidence", []) if isinstance(plan, dict) else []
    sub_questions = plan.get("sub_questions", []) if isinstance(plan, dict) else []
    pool = state.get("evidence_pool") or []

    async with span(
        "node.skeptic",
        input={
            "claim": claim,
            "round": rnum,
            "evidence_count": len(evidence),
            "pool_size": len(pool),
        },
    ):
        system = load("skeptic")
        user_parts = [
            f"## Claim\n{claim}",
            f"## Current round\n{rnum}",
        ]
        if sub_questions:
            user_parts.append(
                "## Sub-questions from the plan\n" + "\n".join(f"- {q}" for q in sub_questions)
            )
        if pool:
            user_parts.append(
                "## Preferred evidence pool (cite with [cert:<id>])\n" + format_evidence_pool(pool)
            )
        user_parts.append("## Evidence\n" + format_evidence(evidence))
        prior = format_prior_rounds(state.get("rounds", []))
        if prior:
            user_parts.append(f"## Prior rounds of this debate\n{prior}")
        task = (
            "## Your task\n"
            f"Argue that the claim is FALSE or unsupported for round {rnum}, "
            "following the rules in your system prompt. Cite at least one "
            "specific source by title, or explicitly note that the evidence "
            "does not substantiate the claim."
        )
        if pool:
            task += (
                " When you use an item from the evidence pool, cite it by its "
                "`[cert:<uuid>]` marker so the citation is machine-verifiable."
            )
        user_parts.append(task)
        user = "\n\n".join(user_parts)

        argument = await chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )
        citations = build_round_citations(argument, pool=pool, evidence=evidence)
        entry: dict[str, Any] = {
            "side": "skeptic",
            "round": rnum,
            "argument": argument,
            "evidence": list(evidence),
            "citations": citations,
        }
        update_current_span(
            output={
                "round": rnum,
                "argument_length": len(argument),
                "argument": argument,
                "citation_count": len(citations),
            }
        )
        return {"rounds": [entry]}
