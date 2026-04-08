"""Plan node — decomposes a claim into sub-questions and search queries."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from paper_trail.agents.state import DebateState
from paper_trail.agents.tools.search import search
from paper_trail.core.llm import chat_json
from paper_trail.core.prompts import load


class PlanSchema(BaseModel):
    sub_questions: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)


async def plan(state: DebateState) -> dict[str, Any]:
    """Decompose a claim into sub-questions and seed evidence via search."""
    system = load("plan")
    claim = state.get("claim", "")
    result = await chat_json(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Decompose this claim: {claim}"},
        ],
        PlanSchema,
    )
    evidence: list[dict[str, Any]] = []
    for q in getattr(result, "search_queries", []) or []:
        try:
            hits = await search(q)
        except Exception:
            continue
        for h in hits:
            evidence.append(
                {
                    "title": h.title,
                    "url": h.url,
                    "snippet": h.snippet,
                    "published_date": h.published_date,
                }
            )
    return {
        "plan": {
            "sub_questions": list(getattr(result, "sub_questions", []) or []),
            "search_queries": list(getattr(result, "search_queries", []) or []),
            "evidence": evidence,
        }
    }
