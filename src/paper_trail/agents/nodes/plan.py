"""Plan node — decomposes a claim into sub-questions and search queries."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from paper_trail.agents.nodes._format import format_evidence_pool
from paper_trail.agents.state import DebateState
from paper_trail.agents.tools.fetch import fetch
from paper_trail.agents.tools.search import search
from paper_trail.core.config import settings
from paper_trail.core.langfuse import span, update_current_span
from paper_trail.core.llm import chat_json
from paper_trail.core.prompts import load


class PlanSchema(BaseModel):
    sub_questions: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)


async def plan(state: DebateState) -> dict[str, Any]:
    """Decompose a claim into sub-questions and seed evidence via search."""
    claim = state.get("claim", "")
    pool = state.get("evidence_pool") or []
    async with span(
        "node.plan",
        input={"claim": claim, "pool_size": len(pool)},
    ):
        system = load("plan")
        # When the caller provides pre-collected evidence, nudge the planner
        # to prefer it over fresh Tavily searches for its sub-questions.
        # When absent, preserve the exact wording of the original user
        # message so existing debates behave identically.
        if pool:
            pool_block = format_evidence_pool(pool)
            user_msg = (
                f"Decompose this claim: {claim}\n\n"
                "Draft sub-questions that can be answered from the "
                "following pre-collected evidence pool. Only emit a "
                "`search_queries` entry for sub-questions the pool cannot "
                "answer.\n\n"
                f"## Evidence pool\n{pool_block}"
            )
        else:
            user_msg = f"Decompose this claim: {claim}"
        result = await chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            PlanSchema,
        )
        sub_questions = list(getattr(result, "sub_questions", []) or [])
        search_queries = list(getattr(result, "search_queries", []) or [])

        evidence: list[dict[str, Any]] = []
        failed_queries: list[dict[str, str]] = []
        # OPT-1: the searches are independent, so run them concurrently — the
        # plan node is on the critical path (START->plan->fan-out) and
        # sequential awaits made its latency the sum of every query.
        if search_queries:
            search_results = await asyncio.gather(
                *(search(q) for q in search_queries),
                return_exceptions=True,
            )
            for q, res in zip(search_queries, search_results, strict=True):
                if isinstance(res, BaseException):
                    failed_queries.append({"query": q, "error": f"{type(res).__name__}: {res}"})
                    continue
                for h in res:
                    evidence.append(
                        {
                            "title": h.title,
                            "url": h.url,
                            "snippet": h.snippet,
                            "published_date": h.published_date,
                        }
                    )

        # Ground arguments in the actual article body, not just Tavily's short
        # snippet: fetch the top-N unique-URL hits concurrently and attach the
        # extracted main-content text. A failed fetch leaves the snippet-only
        # entry intact so the debate still runs.
        fetched_count = 0
        top_n = settings.evidence_fetch_top_n
        if top_n > 0 and evidence:
            seen_urls: set[str] = set()
            to_fetch: list[dict[str, Any]] = []
            for item in evidence:
                url = str(item.get("url") or "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    to_fetch.append(item)
                if len(to_fetch) >= top_n:
                    break
            if to_fetch:
                fetched = await asyncio.gather(
                    *(fetch(str(item["url"])) for item in to_fetch),
                    return_exceptions=True,
                )
                limit = settings.evidence_fetch_char_limit
                for item, doc in zip(to_fetch, fetched, strict=True):
                    if isinstance(doc, BaseException):
                        failed_queries.append(
                            {
                                "query": f"fetch:{item.get('url')}",
                                "error": f"{type(doc).__name__}: {doc}",
                            }
                        )
                        continue
                    text = (doc.text or "").strip()
                    if text:
                        item["text"] = text[:limit]
                        fetched_count += 1
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
                "fetched_article_count": fetched_count,
                "failed_query_count": len(failed_queries),
                "sub_questions": sub_questions,
                "search_queries": search_queries,
            },
            metadata={"failed_queries": failed_queries} if failed_queries else None,
        )
        return {"plan": plan_payload}
