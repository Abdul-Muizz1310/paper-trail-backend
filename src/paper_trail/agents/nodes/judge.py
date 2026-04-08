"""Judge node — scores confidence, emits verdict, decides convergence."""

from __future__ import annotations

from paper_trail.agents.state import DebateState


async def judge(state: DebateState) -> dict:  # type: ignore[type-arg]
    raise NotImplementedError
