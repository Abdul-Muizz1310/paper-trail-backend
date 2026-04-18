"""Unit tests for agents/nodes/_citations.py — pure citation resolver."""

from __future__ import annotations

from uuid import uuid4

from paper_trail.agents.nodes._citations import build_round_citations


def test_empty_inputs_yield_empty() -> None:
    assert build_round_citations("argument", pool=[], evidence=[]) == []


def test_cert_marker_resolves_against_pool() -> None:
    uid = uuid4()
    pool = [
        {
            "certificate_id": str(uid),
            "url": "https://x",
            "title": "Pool title",
            "text": "body",
        }
    ]
    citations = build_round_citations(
        f"Claim is grounded [cert:{uid}].",
        pool=pool,
        evidence=[],
    )
    assert citations == [{"type": "cert", "ref": str(uid), "title": "Pool title"}]


def test_invented_cert_marker_dropped() -> None:
    real = uuid4()
    invented = uuid4()
    pool = [
        {"certificate_id": str(real), "url": "u", "title": "R", "text": "t"},
    ]
    citations = build_round_citations(
        f"[cert:{real}] and [cert:{invented}]",
        pool=pool,
        evidence=[],
    )
    refs = [c["ref"] for c in citations]
    assert str(real) in refs
    assert str(invented) not in refs


def test_url_citation_only_when_url_in_argument() -> None:
    evidence = [
        {"title": "Cited", "url": "https://cited.example"},
        {"title": "Uncited", "url": "https://not.in.argument"},
    ]
    citations = build_round_citations(
        "See https://cited.example for details.",
        pool=[],
        evidence=evidence,
    )
    refs = [c["ref"] for c in citations]
    assert "https://cited.example" in refs
    assert "https://not.in.argument" not in refs


def test_cert_citations_come_before_url_citations() -> None:
    uid = uuid4()
    pool = [{"certificate_id": str(uid), "url": "p", "title": "P", "text": "b"}]
    evidence = [{"title": "U", "url": "https://u"}]
    citations = build_round_citations(
        f"[cert:{uid}] says https://u.",
        pool=pool,
        evidence=evidence,
    )
    assert citations[0]["type"] == "cert"
    assert citations[1]["type"] == "url"


def test_dedup_repeated_cert_marker() -> None:
    uid = uuid4()
    pool = [{"certificate_id": str(uid), "url": "p", "title": "P", "text": "b"}]
    citations = build_round_citations(
        f"[cert:{uid}] and again [cert:{uid}]",
        pool=pool,
        evidence=[],
    )
    assert len(citations) == 1


def test_pool_item_with_invalid_cert_id_skipped() -> None:
    real = uuid4()
    pool = [
        {"certificate_id": "not-a-uuid", "url": "u", "title": "Bad", "text": "t"},
        {"certificate_id": str(real), "url": "u2", "title": "Good", "text": "t"},
    ]
    citations = build_round_citations(
        f"[cert:{real}] is fine.",
        pool=pool,
        evidence=[],
    )
    assert [c["ref"] for c in citations] == [str(real)]


def test_pool_item_missing_cert_id_skipped() -> None:
    uid = uuid4()
    pool = [
        {"url": "u", "title": "No id", "text": "t"},
        {"certificate_id": str(uid), "url": "u2", "title": "OK", "text": "t"},
    ]
    citations = build_round_citations(
        f"[cert:{uid}]",
        pool=pool,
        evidence=[],
    )
    assert [c["ref"] for c in citations] == [str(uid)]


def test_non_dict_pool_item_skipped() -> None:
    pool = ["not a dict"]  # type: ignore[list-item]
    citations = build_round_citations("plain", pool=pool, evidence=[])  # type: ignore[arg-type]
    assert citations == []


def test_non_dict_evidence_item_skipped() -> None:
    evidence = [42, "str", {"url": "https://u", "title": "T"}]  # type: ignore[list-item]
    citations = build_round_citations(
        "https://u",
        pool=[],
        evidence=evidence,  # type: ignore[arg-type]
    )
    assert [c["ref"] for c in citations] == ["https://u"]


def test_dedup_repeated_url() -> None:
    evidence = [
        {"url": "https://u", "title": "T"},
        {"url": "https://u", "title": "T2"},
    ]
    citations = build_round_citations("https://u", pool=[], evidence=evidence)
    assert len(citations) == 1


def test_url_citation_falls_back_to_url_as_title() -> None:
    evidence = [{"url": "https://u"}]
    citations = build_round_citations("see https://u", pool=[], evidence=evidence)
    assert citations[0]["title"] == "https://u"


def test_evidence_without_url_skipped() -> None:
    evidence = [{"title": "no url"}]
    citations = build_round_citations("argument", pool=[], evidence=evidence)
    assert citations == []
