"""Unit tests for the eval runner.

Note: real-mode (`run_real`) is intentionally NOT unit-tested here. It depends on
live Neon + OpenRouter and is exercised manually before tagging v0.1.0.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.run_eval import (
    build_report,
    compute_metrics,
    load_claims,
    run_dry,
    write_report,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CLAIMS_PATH = REPO_ROOT / "evals" / "claims.yaml"
REPORT_PATH = REPO_ROOT / "evals" / "report.md"

VALID_VERDICTS = {"TRUE", "FALSE", "INCONCLUSIVE"}


def test_load_claims_returns_25_typed_entries() -> None:
    claims = load_claims(CLAIMS_PATH)
    assert len(claims) == 25
    seen_ids: set[int] = set()
    for c in claims:
        assert "id" in c and isinstance(c["id"], int)
        assert "claim" in c and isinstance(c["claim"], str) and c["claim"].strip()
        assert "expected" in c and c["expected"] in VALID_VERDICTS
        assert c["id"] not in seen_ids
        seen_ids.add(c["id"])


def test_compute_metrics_accuracy_avg_rounds_p95() -> None:
    results = [
        {"id": i, "expected": "TRUE", "actual_verdict": "TRUE", "rounds": 2, "wall_ms": 100.0}
        for i in range(10)
    ] + [
        {"id": 10, "expected": "TRUE", "actual_verdict": "FALSE", "rounds": 4, "wall_ms": 500.0},
        {"id": 11, "expected": "FALSE", "actual_verdict": "TRUE", "rounds": 5, "wall_ms": 900.0},
    ]
    metrics = compute_metrics(results)
    assert metrics["accuracy"] == pytest.approx(10 / 12)
    expected_avg = (2 * 10 + 4 + 5) / 12
    assert metrics["avg_rounds"] == pytest.approx(expected_avg)
    # p95 by sorted index: sorted=[100*10,500,900]; idx=int(12*0.95)=11 -> 900
    assert metrics["p95_ms"] == pytest.approx(900.0)


def test_build_report_contains_required_sections_and_rows() -> None:
    results = [
        {
            "id": 1,
            "claim": "Water is wet.",
            "expected": "TRUE",
            "actual_verdict": "TRUE",
            "confidence": 0.9,
            "rounds": 2,
            "wall_ms": 123.0,
        },
        {
            "id": 2,
            "claim": "Sky is green.",
            "expected": "FALSE",
            "actual_verdict": "FALSE",
            "confidence": 0.88,
            "rounds": 3,
            "wall_ms": 456.0,
        },
    ]
    md = build_report(results, mode="dry-run")
    assert "# paper-trail eval report" in md
    assert "## Summary" in md
    assert "## Details" in md
    assert "dry-run" in md
    # one row per result (id appears in a table cell)
    assert "| 1 |" in md
    assert "| 2 |" in md


def test_run_dry_returns_25_results_no_network() -> None:
    claims = load_claims(CLAIMS_PATH)
    results = run_dry(claims)
    assert len(results) == 25
    for r in results:
        for key in ("id", "claim", "expected", "actual_verdict", "confidence", "rounds", "wall_ms"):
            assert key in r
        assert r["actual_verdict"] in VALID_VERDICTS


def test_write_report_roundtrip(tmp_path: Path) -> None:
    results = [
        {
            "id": 1,
            "claim": "x",
            "expected": "TRUE",
            "actual_verdict": "TRUE",
            "confidence": 0.9,
            "rounds": 1,
            "wall_ms": 10.0,
        }
    ]
    out = tmp_path / "report.md"
    write_report(results, mode="dry-run", path=out)
    content = out.read_text(encoding="utf-8")
    assert content == build_report(results, mode="dry-run")


def test_placeholder_report_md_has_required_headers() -> None:
    assert REPORT_PATH.exists()
    text = REPORT_PATH.read_text(encoding="utf-8")
    assert "# paper-trail eval report" in text
    assert "## Summary" in text
    assert "## Details" in text
