"""Unit tests for agents/state.py."""

from __future__ import annotations

import operator

import pytest

from paper_trail.agents.state import (
    CONFIDENCE_THRESHOLD,
    MAX_CLAIM_LEN,
    DebateState,
    initial_state,
    is_converged,
    validate_state,
)


class TestInitialState:
    def test_valid(self) -> None:
        s = initial_state("the sky is blue", 5)
        assert s["claim"] == "the sky is blue"
        assert s["max_rounds"] == 5
        assert s["round"] == 0
        assert s["rounds"] == []
        assert s["verdict"] is None
        assert s["confidence"] is None

    def test_empty_claim(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            initial_state("", 5)

    def test_whitespace_claim(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            initial_state("   ", 5)

    def test_too_long_claim(self) -> None:
        with pytest.raises(ValueError, match="<="):
            initial_state("x" * (MAX_CLAIM_LEN + 1), 5)

    def test_zero_max_rounds(self) -> None:
        with pytest.raises(ValueError, match="max_rounds"):
            initial_state("claim", 0)

    def test_negative_max_rounds(self) -> None:
        with pytest.raises(ValueError):
            initial_state("claim", -1)


class TestIsConverged:
    def test_max_rounds_reached(self) -> None:
        s: DebateState = {"round": 5, "max_rounds": 5, "confidence": 0.1}
        assert is_converged(s)

    def test_threshold_reached(self) -> None:
        s: DebateState = {"round": 1, "max_rounds": 5, "confidence": CONFIDENCE_THRESHOLD}
        assert is_converged(s)

    def test_below_threshold(self) -> None:
        s: DebateState = {"round": 1, "max_rounds": 5, "confidence": 0.5}
        assert not is_converged(s)

    def test_no_confidence(self) -> None:
        s: DebateState = {"round": 0, "max_rounds": 5}
        assert not is_converged(s)


class TestValidateState:
    def test_valid_noop(self) -> None:
        validate_state({"confidence": 0.5, "verdict": "TRUE"})

    def test_bad_confidence_high(self) -> None:
        with pytest.raises(ValueError):
            validate_state({"confidence": 1.5})

    def test_bad_confidence_low(self) -> None:
        with pytest.raises(ValueError):
            validate_state({"confidence": -0.1})

    def test_bad_verdict(self) -> None:
        with pytest.raises(ValueError):
            validate_state({"verdict": "MAYBE"})  # type: ignore[typeddict-item]


def test_rounds_reducer_appends() -> None:
    """The reducer on rounds is operator.add which concatenates lists."""
    a = [{"side": "proponent", "round": 1, "argument": "x", "evidence": []}]
    b = [{"side": "skeptic", "round": 1, "argument": "y", "evidence": []}]
    assert operator.add(a, b) == a + b
