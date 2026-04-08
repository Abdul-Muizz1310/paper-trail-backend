"""Unit tests for agents/tools/cite.py."""

from __future__ import annotations

from paper_trail.agents.tools.cite import format_citation
from paper_trail.agents.tools.search import SearchHit


def test_cite_with_date() -> None:
    hit = SearchHit(
        title="Climate Report",
        url="https://example.com/r",
        snippet="s",
        published_date="2025-01-02",
    )
    out = format_citation(hit)
    assert "Climate Report" in out
    assert "https://example.com/r" in out
    assert "2025-01-02" in out
    assert out.startswith("[")


def test_cite_without_date() -> None:
    hit = SearchHit(title="Report", url="https://example.com/r", snippet="s")
    out = format_citation(hit)
    assert out == "[Report](https://example.com/r)"
