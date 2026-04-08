"""Unit tests for agents/tools/fetch.py."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from paper_trail.agents.tools.fetch import FetchedDoc, fetch
from paper_trail.core.errors import ToolError

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_article.html"


@respx.mock
async def test_fetch_happy_path() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    respx.get("https://example.com/article").mock(
        return_value=httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )
    doc = await fetch("https://example.com/article")
    assert isinstance(doc, FetchedDoc)
    assert doc.url == "https://example.com/article"
    assert "main content" in doc.text.lower()
    assert len(doc.text) > 100


@respx.mock
async def test_fetch_404_raises() -> None:
    respx.get("https://example.com/missing").mock(return_value=httpx.Response(404))
    with pytest.raises(ToolError):
        await fetch("https://example.com/missing")


@respx.mock
async def test_fetch_empty_body_raises() -> None:
    respx.get("https://example.com/empty").mock(
        return_value=httpx.Response(200, text="", headers={"content-type": "text/html"})
    )
    with pytest.raises(ToolError):
        await fetch("https://example.com/empty")
