"""Unit tests for agents/nodes/_format.py.

Covers both formatters exhaustively — happy path, boundary conditions, and
negative-space (malformed input, missing fields, truncation) — driving the
module from 24% → 100% coverage.
"""

from __future__ import annotations

from paper_trail.agents.nodes._format import (
    _MAX_EVIDENCE_ITEMS,
    _MAX_SNIPPET_CHARS,
    format_evidence,
    format_prior_rounds,
)

# ---------------------------------------------------------------------------
# format_evidence
# ---------------------------------------------------------------------------


def test_format_evidence_none_returns_placeholder() -> None:
    assert format_evidence(None) == "_No evidence gathered._"


def test_format_evidence_empty_list_returns_placeholder() -> None:
    assert format_evidence([]) == "_No evidence gathered._"


def test_format_evidence_single_full_item() -> None:
    evidence = [
        {
            "title": "NASA Fact Sheet",
            "url": "https://nasa.gov/facts",
            "snippet": "The Great Wall is not visible from low Earth orbit.",
        }
    ]
    out = format_evidence(evidence)
    assert "1. **NASA Fact Sheet**" in out
    assert "— https://nasa.gov/facts" in out
    assert "The Great Wall is not visible from low Earth orbit." in out


def test_format_evidence_numbers_items_sequentially() -> None:
    evidence = [
        {"title": "A", "url": "https://a", "snippet": "sa"},
        {"title": "B", "url": "https://b", "snippet": "sb"},
        {"title": "C", "url": "https://c", "snippet": "sc"},
    ]
    out = format_evidence(evidence)
    assert "1. **A**" in out
    assert "2. **B**" in out
    assert "3. **C**" in out


def test_format_evidence_missing_title_uses_placeholder() -> None:
    out = format_evidence([{"url": "https://x", "snippet": "hi"}])
    assert "**(untitled)**" in out


def test_format_evidence_missing_url_omits_dash() -> None:
    out = format_evidence([{"title": "T", "snippet": "s"}])
    assert "**T**" in out
    assert "—" not in out


def test_format_evidence_missing_snippet_omits_snippet_line() -> None:
    out = format_evidence([{"title": "T", "url": "https://x"}])
    lines = out.splitlines()
    assert len(lines) == 1
    assert "**T**" in lines[0]


def test_format_evidence_truncates_long_snippet() -> None:
    long_snippet = "x" * (_MAX_SNIPPET_CHARS + 100)
    out = format_evidence([{"title": "T", "url": "https://x", "snippet": long_snippet}])
    assert "…" in out
    # snippet segment should be ≤ _MAX_SNIPPET_CHARS + ellipsis
    snippet_line = next(line for line in out.splitlines() if line.startswith("   "))
    assert len(snippet_line.strip()) <= _MAX_SNIPPET_CHARS + 1


def test_format_evidence_truncates_to_max_items_with_remainder_note() -> None:
    evidence = [
        {"title": f"T{i}", "url": f"https://x/{i}", "snippet": "s"}
        for i in range(_MAX_EVIDENCE_ITEMS + 3)
    ]
    out = format_evidence(evidence)
    assert f"**T{_MAX_EVIDENCE_ITEMS - 1}**" in out
    assert f"**T{_MAX_EVIDENCE_ITEMS}**" not in out
    assert "(3 more item(s) omitted)" in out


def test_format_evidence_exactly_max_items_no_remainder_note() -> None:
    evidence = [
        {"title": f"T{i}", "url": f"https://x/{i}", "snippet": "s"}
        for i in range(_MAX_EVIDENCE_ITEMS)
    ]
    out = format_evidence(evidence)
    assert "more item(s) omitted" not in out


def test_format_evidence_non_dict_items_are_skipped() -> None:
    evidence = [
        "not a dict",  # type: ignore[list-item]
        42,  # type: ignore[list-item]
        None,  # type: ignore[list-item]
        {"title": "Valid", "url": "https://v", "snippet": "s"},
    ]
    out = format_evidence(evidence)  # type: ignore[arg-type]
    assert "**Valid**" in out
    assert "not a dict" not in out


def test_format_evidence_all_non_dict_returns_placeholder() -> None:
    evidence = ["bad", 1, None]  # type: ignore[list-item]
    out = format_evidence(evidence)  # type: ignore[arg-type]
    assert out == "_No evidence gathered._"


def test_format_evidence_accepts_generator() -> None:
    def gen() -> object:
        yield {"title": "G", "url": "https://g", "snippet": "s"}

    out = format_evidence(gen())  # type: ignore[arg-type]
    assert "**G**" in out


def test_format_evidence_whitespace_only_title_and_url_are_stripped() -> None:
    out = format_evidence([{"title": "  T  ", "url": "  https://x  ", "snippet": "  s  "}])
    assert "**T**" in out
    assert "— https://x" in out
    assert "   s" in out  # leading indent + stripped snippet


# ---------------------------------------------------------------------------
# format_prior_rounds
# ---------------------------------------------------------------------------


def test_format_prior_rounds_none_returns_empty_string() -> None:
    assert format_prior_rounds(None) == ""


def test_format_prior_rounds_empty_list_returns_empty_string() -> None:
    assert format_prior_rounds([]) == ""


def test_format_prior_rounds_renders_single_round_with_header() -> None:
    rounds = [
        {"side": "proponent", "round": 1, "argument": "yes", "evidence": []},
        {"side": "skeptic", "round": 1, "argument": "no", "evidence": []},
    ]
    out = format_prior_rounds(rounds)
    assert "### Round 1" in out
    assert "**Proponent:** yes" in out
    assert "**Skeptic:** no" in out


def test_format_prior_rounds_proponent_appears_before_skeptic() -> None:
    # Deliberately put skeptic first in the input.
    rounds = [
        {"side": "skeptic", "round": 1, "argument": "skep"},
        {"side": "proponent", "round": 1, "argument": "prop"},
    ]
    out = format_prior_rounds(rounds)
    assert out.index("**Proponent:** prop") < out.index("**Skeptic:** skep")


def test_format_prior_rounds_sorts_by_round_number() -> None:
    rounds = [
        {"side": "proponent", "round": 3, "argument": "p3"},
        {"side": "proponent", "round": 1, "argument": "p1"},
        {"side": "proponent", "round": 2, "argument": "p2"},
    ]
    out = format_prior_rounds(rounds)
    idx1 = out.index("### Round 1")
    idx2 = out.index("### Round 2")
    idx3 = out.index("### Round 3")
    assert idx1 < idx2 < idx3


def test_format_prior_rounds_non_dict_items_are_skipped() -> None:
    rounds = [
        "not a dict",
        42,
        {"side": "proponent", "round": 1, "argument": "ok"},
    ]
    out = format_prior_rounds(rounds)  # type: ignore[arg-type]
    assert "**Proponent:** ok" in out
    assert "not a dict" not in out


def test_format_prior_rounds_missing_side_renders_as_question_mark() -> None:
    rounds = [{"round": 1, "argument": "mystery"}]
    out = format_prior_rounds(rounds)
    assert "**?:** mystery" in out


def test_format_prior_rounds_missing_argument_renders_empty_body() -> None:
    rounds = [{"side": "proponent", "round": 1}]
    out = format_prior_rounds(rounds)
    assert "**Proponent:** " in out


def test_format_prior_rounds_missing_round_defaults_to_zero() -> None:
    rounds = [{"side": "proponent", "argument": "a"}]
    out = format_prior_rounds(rounds)
    assert "### Round 0" in out


def test_format_prior_rounds_multiple_rounds_multiple_sides() -> None:
    rounds = [
        {"side": "proponent", "round": 1, "argument": "p1"},
        {"side": "skeptic", "round": 1, "argument": "s1"},
        {"side": "proponent", "round": 2, "argument": "p2"},
        {"side": "skeptic", "round": 2, "argument": "s2"},
    ]
    out = format_prior_rounds(rounds)
    assert out.count("### Round ") == 2
    assert "**Proponent:** p1" in out
    assert "**Skeptic:** s1" in out
    assert "**Proponent:** p2" in out
    assert "**Skeptic:** s2" in out


def test_format_prior_rounds_non_proponent_non_skeptic_sides_sort_after_proponent() -> None:
    # Any side that isn't "proponent" gets sort key 1.
    rounds = [
        {"side": "observer", "round": 1, "argument": "o"},
        {"side": "proponent", "round": 1, "argument": "p"},
    ]
    out = format_prior_rounds(rounds)
    assert out.index("**Proponent:** p") < out.index("**Observer:** o")


def test_format_prior_rounds_all_non_dict_returns_empty_string() -> None:
    rounds = ["bad", 1, None]
    out = format_prior_rounds(rounds)  # type: ignore[arg-type]
    assert out == ""
