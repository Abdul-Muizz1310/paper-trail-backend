"""Render node — deterministic markdown transcript."""

from __future__ import annotations

from paper_trail.agents.state import DebateState


async def render(state: DebateState) -> dict:  # type: ignore[type-arg]
    raise NotImplementedError
