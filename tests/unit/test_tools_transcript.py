"""Unit tests for agents/tools/transcript.py — pure hash + cert-marker helpers."""

from __future__ import annotations

from uuid import UUID, uuid4

from paper_trail.agents.tools.transcript import (
    canonical_transcript_json,
    extract_cert_markers,
    hash_transcript,
)


class TestExtractCertMarkers:
    def test_returns_empty_when_no_markers(self) -> None:
        assert extract_cert_markers("plain text with no citations") == []

    def test_extracts_single_uuid(self) -> None:
        uid = uuid4()
        text = f"According to [cert:{uid}] the claim holds."
        assert extract_cert_markers(text) == [uid]

    def test_extracts_multiple_preserves_order(self) -> None:
        u1, u2 = uuid4(), uuid4()
        text = f"A [cert:{u1}] then B [cert:{u2}]."
        assert extract_cert_markers(text) == [u1, u2]

    def test_deduplicates(self) -> None:
        uid = uuid4()
        text = f"[cert:{uid}] and again [cert:{uid}]"
        assert extract_cert_markers(text) == [uid]

    def test_drops_invalid_uuid(self) -> None:
        # 36-char body that matches the regex shape but isn't a valid UUID.
        # UUID() constructor rejects strings of all dashes; the parser
        # drops them silently rather than emitting a bogus ref.
        text = "bogus [cert:------------------------------------]"
        assert extract_cert_markers(text) == []

    def test_case_insensitive_prefix(self) -> None:
        uid = uuid4()
        text = f"[CERT:{uid}]"
        assert extract_cert_markers(text) == [uid]


class TestCanonicalJson:
    def test_deterministic_for_same_inputs(self) -> None:
        rounds = [
            {"side": "proponent", "round": 1, "argument_md": "a", "citations": []},
            {"side": "skeptic", "round": 1, "argument_md": "b", "citations": []},
        ]
        a = canonical_transcript_json(
            claim="c",
            verdict="TRUE",
            confidence=0.9,
            rounds=rounds,
        )
        b = canonical_transcript_json(
            claim="c",
            verdict="TRUE",
            confidence=0.9,
            rounds=rounds,
        )
        assert a == b

    def test_key_order_normalized(self) -> None:
        # Two equivalent rounds built with different dict insertion order
        # must canonicalize to the same string.
        r1 = [{"side": "proponent", "round": 1, "argument_md": "a", "citations": []}]
        r2 = [{"argument_md": "a", "citations": [], "round": 1, "side": "proponent"}]
        a = canonical_transcript_json(claim="c", verdict="TRUE", confidence=0.9, rounds=r1)
        b = canonical_transcript_json(claim="c", verdict="TRUE", confidence=0.9, rounds=r2)
        assert a == b

    def test_no_whitespace(self) -> None:
        out = canonical_transcript_json(claim="c", verdict="TRUE", confidence=0.9, rounds=[])
        assert " " not in out
        assert "\n" not in out

    def test_preserves_non_ascii(self) -> None:
        out = canonical_transcript_json(
            claim="café",
            verdict="TRUE",
            confidence=0.9,
            rounds=[],
        )
        assert "café" in out


class TestHashTranscript:
    def test_returns_64_char_hex(self) -> None:
        h = hash_transcript(claim="c", verdict="TRUE", confidence=0.9, rounds=[])
        assert len(h) == 64
        assert all(ch in "0123456789abcdef" for ch in h)

    def test_deterministic(self) -> None:
        a = hash_transcript(claim="c", verdict="TRUE", confidence=0.9, rounds=[])
        b = hash_transcript(claim="c", verdict="TRUE", confidence=0.9, rounds=[])
        assert a == b

    def test_changes_when_claim_changes(self) -> None:
        a = hash_transcript(claim="c1", verdict="TRUE", confidence=0.9, rounds=[])
        b = hash_transcript(claim="c2", verdict="TRUE", confidence=0.9, rounds=[])
        assert a != b

    def test_changes_when_rounds_change(self) -> None:
        r1: list[dict[str, object]] = []
        r2: list[dict[str, object]] = [{"side": "proponent", "round": 1, "argument_md": "x"}]
        a = hash_transcript(claim="c", verdict="TRUE", confidence=0.9, rounds=r1)
        b = hash_transcript(claim="c", verdict="TRUE", confidence=0.9, rounds=r2)
        assert a != b


def test_uuid_type_is_uuid() -> None:
    uid = uuid4()
    out = extract_cert_markers(f"[cert:{uid}]")
    assert isinstance(out[0], UUID)
