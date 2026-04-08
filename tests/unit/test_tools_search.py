"""Unit tests for agents/tools/search.py."""

from __future__ import annotations

import httpx
import pytest
import respx

from paper_trail.agents.tools.search import SearchHit, search
from paper_trail.core.errors import ToolError

TAVILY_URL = "https://api.tavily.com/search"


@respx.mock
async def test_search_happy_path() -> None:
    payload = {
        "results": [
            {
                "title": "First",
                "url": "https://a.example/1",
                "content": "snippet one",
                "published_date": "2025-01-01",
            },
            {
                "title": "Second",
                "url": "https://a.example/2",
                "content": "snippet two",
            },
        ]
    }
    respx.post(TAVILY_URL).mock(return_value=httpx.Response(200, json=payload))
    hits = await search("climate change")
    assert len(hits) == 2
    assert isinstance(hits[0], SearchHit)
    assert hits[0].title == "First"
    assert hits[0].url == "https://a.example/1"
    assert hits[0].snippet == "snippet one"
    assert hits[0].published_date == "2025-01-01"
    assert hits[1].published_date is None


@respx.mock
async def test_search_empty_results() -> None:
    respx.post(TAVILY_URL).mock(return_value=httpx.Response(200, json={"results": []}))
    hits = await search("nothing")
    assert hits == []


@respx.mock
async def test_search_server_error_raises() -> None:
    respx.post(TAVILY_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(ToolError):
        await search("bad")
