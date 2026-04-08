"""Citation formatting — pure function."""

from __future__ import annotations

from paper_trail.agents.tools.search import SearchHit


def format_citation(hit: SearchHit) -> str:
    raise NotImplementedError
