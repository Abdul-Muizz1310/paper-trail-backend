"""Tavily web search."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from paper_trail.core import tracing
from paper_trail.core.config import settings
from paper_trail.core.errors import ToolError

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
    with tracing.client().start_as_current_observation(
        as_type="tool", name="search-web", input={"query": query, "k": k}
    ) as observation:
        try:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(_TAVILY_URL, json=payload)
            except httpx.HTTPError as e:
                raise ToolError("tavily_http_error", str(e)) from e
            if resp.status_code != 200:
                raise ToolError("tavily_error", f"status={resp.status_code}")
        except ToolError as e:
            observation.update(level="ERROR", status_message=str(e))
            raise
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
        observation.update(output={"hit_count": len(hits), "titles": [h.title for h in hits]})
        return hits
