"""Plan node — decomposes a claim into sub-questions and search queries."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from paper_trail.agents.state import DebateState
from paper_trail.agents.tools.search import search
from paper_trail.core.langfuse import span, update_current_span
from paper_trail.core.llm import chat_json
from paper_trail.core.prompts import load


class PlanSchema(BaseModel):
    sub_questions: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)


async def plan(state: DebateState) -> dict[str, Any]:
    """Decompose a claim into sub-questions and seed evidence via search."""
    claim = state.get("claim", "")
    async with span("node.plan", input={"claim": claim}):
        system = load("plan")
        result = await chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Decompose this claim: {claim}"},
            ],
            PlanSchema,
        )
        sub_questions = list(getattr(result, "sub_questions", []) or [])
        search_queries = list(getattr(result, "search_queries", []) or [])

        evidence: list[dict[str, Any]] = []
        failed_queries: list[dict[str, str]] = []
        for q in search_queries:
            try:
                hits = await search(q)
            except Exception as exc:
                failed_queries.append({"query": q, "error": f"{type(exc).__name__}: {exc}"})
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
        plan_payload = {
            "sub_questions": sub_questions,
            "search_queries": search_queries,
            "evidence": evidence,
        }
        update_current_span(
            output={
                "sub_question_count": len(sub_questions),
                "search_query_count": len(search_queries),
                "evidence_count": len(evidence),
                "failed_query_count": len(failed_queries),
                "sub_questions": sub_questions,
                "search_queries": search_queries,
            },
            metadata={"failed_queries": failed_queries} if failed_queries else None,
        )
        return {"plan": plan_payload}
