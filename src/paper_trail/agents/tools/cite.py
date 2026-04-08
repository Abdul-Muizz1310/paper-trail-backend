"""Citation formatting — pure function."""

from __future__ import annotations

from paper_trail.agents.tools.search import SearchHit


def format_citation(hit: SearchHit) -> str:
    """Format a SearchHit as a markdown citation."""
    base = f"[{hit.title}]({hit.url})"
    if hit.published_date:
        return f"{base} — {hit.published_date}"
    return base
