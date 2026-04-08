"""Unit tests for agents/nodes/render.py."""

from __future__ import annotations

from paper_trail.agents.nodes.render import render


async def test_render_builds_markdown() -> None:
    state = {
        "claim": "the sky is blue",
        "max_rounds": 3,
        "round": 2,
        "rounds": [
            {"side": "proponent", "round": 1, "argument": "yes because rayleigh", "evidence": []},
            {"side": "skeptic", "round": 1, "argument": "sometimes it is not", "evidence": []},
        ],
        "verdict": "TRUE",
        "confidence": 0.9,
    }
    out = await render(state)
    md = out["transcript_md"]
    assert "the sky is blue" in md
    assert "rayleigh" in md
    assert "sometimes it is not" in md
    assert "TRUE" in md


async def test_render_deterministic() -> None:
    state = {
        "claim": "x",
        "max_rounds": 1,
        "round": 1,
        "rounds": [
            {"side": "proponent", "round": 1, "argument": "a", "evidence": []},
        ],
        "verdict": "FALSE",
        "confidence": 0.5,
    }
    a = await render(state)
    b = await render(state)
    assert a["transcript_md"] == b["transcript_md"]


async def test_render_empty_rounds() -> None:
    out = await render(
        {
            "claim": "c",
            "max_rounds": 1,
            "round": 0,
            "rounds": [],
            "verdict": None,
            "confidence": None,
        }
    )
    assert "c" in out["transcript_md"]
