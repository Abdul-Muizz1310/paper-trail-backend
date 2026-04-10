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
    assert "_No rounds run._" in out["transcript_md"]


async def test_render_evidence_rendered_with_title_and_url() -> None:
    state = {
        "claim": "c",
        "max_rounds": 1,
        "round": 1,
        "rounds": [
            {
                "side": "proponent",
                "round": 1,
                "argument": "a",
                "evidence": [
                    {"title": "NASA", "url": "https://nasa.gov"},
                    {"title": "MIT", "url": "https://mit.edu"},
                ],
            }
        ],
        "verdict": "TRUE",
        "confidence": 0.9,
    }
    md = (await render(state))["transcript_md"]
    assert "**Evidence:**" in md
    assert "- [NASA](https://nasa.gov)" in md
    assert "- [MIT](https://mit.edu)" in md


async def test_render_evidence_missing_title_falls_back_to_url() -> None:
    state = {
        "claim": "c",
        "max_rounds": 1,
        "round": 1,
        "rounds": [
            {
                "side": "proponent",
                "round": 1,
                "argument": "a",
                "evidence": [{"url": "https://example.com"}],
            }
        ],
        "verdict": "TRUE",
        "confidence": 0.9,
    }
    md = (await render(state))["transcript_md"]
    assert "- [https://example.com](https://example.com)" in md


async def test_render_evidence_non_dict_items_are_skipped() -> None:
    state = {
        "claim": "c",
        "max_rounds": 1,
        "round": 1,
        "rounds": [
            {
                "side": "proponent",
                "round": 1,
                "argument": "a",
                "evidence": [
                    "not a dict",
                    42,
                    {"title": "OK", "url": "https://ok"},
                ],
            }
        ],
        "verdict": "TRUE",
        "confidence": 0.9,
    }
    md = (await render(state))["transcript_md"]
    assert "- [OK](https://ok)" in md
    assert "not a dict" not in md


async def test_render_rounds_without_evidence_omit_evidence_header() -> None:
    state = {
        "claim": "c",
        "max_rounds": 1,
        "round": 1,
        "rounds": [
            {"side": "proponent", "round": 1, "argument": "a", "evidence": []},
        ],
        "verdict": "TRUE",
        "confidence": 0.9,
    }
    md = (await render(state))["transcript_md"]
    assert "**Evidence:**" not in md


async def test_render_reasoning_section_included_when_present() -> None:
    state = {
        "claim": "c",
        "max_rounds": 1,
        "round": 1,
        "rounds": [
            {"side": "proponent", "round": 1, "argument": "a", "evidence": []},
        ],
        "verdict": "TRUE",
        "confidence": 0.9,
        "reasoning": "The weight of evidence favors TRUE.",
    }
    md = (await render(state))["transcript_md"]
    assert "## Reasoning" in md
    assert "The weight of evidence favors TRUE." in md


async def test_render_reasoning_section_omitted_when_blank() -> None:
    state = {
        "claim": "c",
        "max_rounds": 1,
        "round": 1,
        "rounds": [
            {"side": "proponent", "round": 1, "argument": "a", "evidence": []},
        ],
        "verdict": "TRUE",
        "confidence": 0.9,
        "reasoning": "   ",  # whitespace only — must not render header
    }
    md = (await render(state))["transcript_md"]
    assert "## Reasoning" not in md


async def test_render_reasoning_none_omitted() -> None:
    state = {
        "claim": "c",
        "max_rounds": 1,
        "round": 1,
        "rounds": [
            {"side": "proponent", "round": 1, "argument": "a", "evidence": []},
        ],
        "verdict": "TRUE",
        "confidence": 0.9,
        "reasoning": None,
    }
    md = (await render(state))["transcript_md"]
    assert "## Reasoning" not in md


async def test_render_multiple_rounds_headers_sorted() -> None:
    state = {
        "claim": "c",
        "max_rounds": 3,
        "round": 3,
        "rounds": [
            {"side": "proponent", "round": 2, "argument": "p2", "evidence": []},
            {"side": "proponent", "round": 1, "argument": "p1", "evidence": []},
            {"side": "skeptic", "round": 1, "argument": "s1", "evidence": []},
            {"side": "skeptic", "round": 2, "argument": "s2", "evidence": []},
        ],
        "verdict": "INCONCLUSIVE",
        "confidence": 0.6,
    }
    md = (await render(state))["transcript_md"]
    assert md.index("## Round 1") < md.index("## Round 2")
    # within a round, proponent sorts before skeptic alphabetically
    r1_start = md.index("## Round 1")
    r2_start = md.index("## Round 2")
    r1_block = md[r1_start:r2_start]
    assert r1_block.index("### Proponent") < r1_block.index("### Skeptic")


async def test_render_verdict_and_confidence_always_present() -> None:
    state = {
        "claim": "c",
        "max_rounds": 1,
        "round": 0,
        "rounds": [],
        "verdict": None,
        "confidence": None,
    }
    md = (await render(state))["transcript_md"]
    assert "## Verdict" in md
    assert "- Verdict: **None**" in md
    assert "- Confidence: None" in md
