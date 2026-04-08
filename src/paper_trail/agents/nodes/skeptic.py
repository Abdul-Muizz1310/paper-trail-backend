"""Skeptic node — argues the claim is FALSE."""

from __future__ import annotations

from paper_trail.agents.state import DebateState


async def skeptic(state: DebateState) -> dict:  # type: ignore[type-arg]
    raise NotImplementedError
