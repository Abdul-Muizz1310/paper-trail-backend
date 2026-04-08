"""Tavily web search with Upstash cache."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    published_date: str | None = None


async def search(query: str, k: int = 5) -> list[SearchHit]:
    raise NotImplementedError
