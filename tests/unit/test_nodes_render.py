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


# ---------------------------------------------------------------------------
# Block 6 (Spec 08) — rounds_struct + transcript_hash
# ---------------------------------------------------------------------------


async def test_render_emits_rounds_struct_and_hash() -> None:
    state = {
        "claim": "c",
        "max_rounds": 1,
        "round": 1,
        "rounds": [
            {
                "side": "proponent",
                "round": 1,
                "argument": "argue",
                "evidence": [],
                "citations": [{"type": "url", "ref": "https://u", "title": "T"}],
            }
        ],
        "verdict": "TRUE",
        "confidence": 0.9,
    }
    out = await render(state)
    assert "transcript_md" in out
    assert "rounds_struct" in out
    assert "transcript_hash" in out
    rs = out["rounds_struct"]
    assert len(rs) == 1
    assert rs[0]["side"] == "proponent"
    assert rs[0]["argument_md"] == "argue"
    assert rs[0]["citations"] == [{"type": "url", "ref": "https://u", "title": "T"}]
    assert len(out["transcript_hash"]) == 64


async def test_render_hash_deterministic_for_same_state() -> None:
    """Case 21: transcript hash stable across re-renders of the same state."""
    state = {
        "claim": "c",
        "max_rounds": 1,
        "round": 1,
        "rounds": [
            {"side": "proponent", "round": 1, "argument": "a", "evidence": [], "citations": []},
            {"side": "skeptic", "round": 1, "argument": "b", "evidence": [], "citations": []},
        ],
        "verdict": "TRUE",
        "confidence": 0.9,
    }
    a = await render(state)
    b = await render(state)
    assert a["transcript_hash"] == b["transcript_hash"]


async def test_render_rounds_struct_order_is_stable() -> None:
    # Feed skeptic first — output must still be proponent then skeptic.
    state = {
        "claim": "c",
        "max_rounds": 1,
        "round": 1,
        "rounds": [
            {"side": "skeptic", "round": 1, "argument": "s", "evidence": [], "citations": []},
            {"side": "proponent", "round": 1, "argument": "p", "evidence": [], "citations": []},
        ],
        "verdict": "TRUE",
        "confidence": 0.9,
    }
    out = await render(state)
    rs = out["rounds_struct"]
    assert [r["side"] for r in rs] == ["proponent", "skeptic"]


async def test_render_hash_differs_when_claim_differs() -> None:
    base = {
        "max_rounds": 1,
        "round": 1,
        "rounds": [],
        "verdict": "TRUE",
        "confidence": 0.9,
    }
    a = await render({**base, "claim": "c1"})
    b = await render({**base, "claim": "c2"})
    assert a["transcript_hash"] != b["transcript_hash"]


async def test_render_drops_malformed_citation_entries() -> None:
    state = {
        "claim": "c",
        "max_rounds": 1,
        "round": 1,
        "rounds": [
            {
                "side": "proponent",
                "round": 1,
                "argument": "a",
                "evidence": [],
                "citations": [
                    "not a dict",
                    {"type": "cert"},  # missing ref
                    {"type": "bogus", "ref": "x"},  # wrong type
                    {"type": "url", "ref": "https://ok", "title": "OK"},
                ],
            }
        ],
        "verdict": "TRUE",
        "confidence": 0.9,
    }
    out = await render(state)
    cites = out["rounds_struct"][0]["citations"]
    assert cites == [{"type": "url", "ref": "https://ok", "title": "OK"}]


async def test_render_handles_none_verdict_in_hash() -> None:
    """None verdict must still produce a valid 64-char hex hash."""
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
    assert len(out["transcript_hash"]) == 64
