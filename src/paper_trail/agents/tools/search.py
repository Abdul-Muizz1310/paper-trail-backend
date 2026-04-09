"""Tavily web search."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from paper_trail.core.config import settings
from paper_trail.core.errors import ToolError
from paper_trail.core.langfuse import span, update_current_span

_TAVILY_URL = "https://api.tavily.com/search"


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    published_date: str | None = None


async def search(query: str, k: int = 5) -> list[SearchHit]:
    """Call Tavily and return typed SearchHits."""
    if not query or not query.strip():
        raise ValueError("query must be non-empty")
    payload = {
        "api_key": settings.tavily_api_key,
        "query": query,
        "max_results": k,
    }
    async with span("tool.search", input={"query": query, "k": k}):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(_TAVILY_URL, json=payload)
        except httpx.HTTPError as e:
            update_current_span(metadata={"error": "tavily_http_error", "error_detail": str(e)})
            raise ToolError("tavily_http_error", str(e)) from e
        if resp.status_code != 200:
            update_current_span(metadata={"error": "tavily_error", "status_code": resp.status_code})
            raise ToolError("tavily_error", f"status={resp.status_code}")
        data = resp.json()
        results = data.get("results") or []
        hits = [
            SearchHit(
                title=str(r.get("title", "")),
                url=str(r.get("url", "")),
                snippet=str(r.get("content", "")),
                published_date=r.get("published_date"),
            )
            for r in results
        ]
        update_current_span(
            output={
                "hit_count": len(hits),
                "titles": [h.title for h in hits],
            },
            metadata={"status_code": resp.status_code},
        )
        return hits
