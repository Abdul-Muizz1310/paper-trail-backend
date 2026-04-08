"""Proponent node — argues the claim is TRUE."""

from __future__ import annotations

from paper_trail.agents.state import DebateState


async def proponent(state: DebateState) -> dict:  # type: ignore[type-arg]
    raise NotImplementedError
