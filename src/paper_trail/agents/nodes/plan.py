"""Plan node — decomposes a claim into sub-questions and search queries."""

from __future__ import annotations

from paper_trail.agents.state import DebateState


async def plan(state: DebateState) -> dict:  # type: ignore[type-arg]
    raise NotImplementedError
