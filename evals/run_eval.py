"""Eval runner for paper-trail.

Usage:
    python -m evals.run_eval [--dry-run] [--max-claims N] [--report-path PATH]

Dry-run mode uses a deterministic stub evaluator (no LLM, no network) so it can
run in CI. Real mode imports DebateService lazily and hits the live graph; it is
only invoked manually against Neon + OpenRouter.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CLAIMS_PATH = REPO_ROOT / "evals" / "claims.yaml"
DEFAULT_REPORT_PATH = REPO_ROOT / "evals" / "report.md"

VERDICTS: tuple[str, str, str] = ("TRUE", "FALSE", "INCONCLUSIVE")
ACCURACY_GATE = 0.80


def load_claims(path: Path = DEFAULT_CLAIMS_PATH) -> list[dict[str, Any]]:
    """Load and validate the claims fixture."""
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, list):
        raise ValueError(f"{path} must contain a YAML list of claims")
    claims: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError(f"claim entry must be a mapping, got {type(entry).__name__}")
        if "id" not in entry or "claim" not in entry or "expected" not in entry:
            raise ValueError(f"claim missing required keys: {entry}")
        if entry["expected"] not in VERDICTS:
            raise ValueError(
                f"claim {entry['id']}: expected must be one of {VERDICTS}, got {entry['expected']}"
            )
        claims.append(cast(dict[str, Any], entry))
    return claims


def _p95(values: list[float]) -> float:
    """Deterministic p95 via sorted-index. Handles small samples."""
    if not values:
        return 0.0
    s = sorted(values)
    idx = int(len(s) * 0.95)
    if idx >= len(s):
        idx = len(s) - 1
    return float(s[idx])


def compute_metrics(results: list[dict[str, Any]]) -> dict[str, float]:
    """Pure metrics aggregation over result rows."""
    if not results:
        return {"accuracy": 0.0, "avg_rounds": 0.0, "p95_ms": 0.0}
    matches = sum(1 for r in results if r["actual_verdict"] == r["expected"])
    accuracy = matches / len(results)
    avg_rounds = statistics.fmean(float(r["rounds"]) for r in results)
    p95_ms = _p95([float(r["wall_ms"]) for r in results])
    return {"accuracy": accuracy, "avg_rounds": avg_rounds, "p95_ms": p95_ms}


def build_report(results: list[dict[str, Any]], mode: str) -> str:
    """Render a deterministic markdown report."""
    metrics = compute_metrics(results)
    timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    lines: list[str] = []
    lines.append("# paper-trail eval report")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Mode: {mode}")
    lines.append(f"- Timestamp: {timestamp}")
    lines.append(f"- Claims: {len(results)}")
    lines.append(f"- Accuracy: {metrics['accuracy']:.3f}")
    lines.append(f"- Avg rounds: {metrics['avg_rounds']:.2f}")
    lines.append(f"- p95 latency (ms): {metrics['p95_ms']:.1f}")
    lines.append("")
    lines.append("## Details")
    lines.append("")
    lines.append("| id | expected | actual | match | confidence | rounds | wall_ms |")
    lines.append("|---:|:---|:---|:---:|---:|---:|---:|")
    for r in results:
        match = "Y" if r["actual_verdict"] == r["expected"] else "N"
        conf = r.get("confidence")
        conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else "-"
        lines.append(
            f"| {r['id']} | {r['expected']} | {r['actual_verdict']} | {match} | "
            f"{conf_s} | {r['rounds']} | {float(r['wall_ms']):.1f} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_report(results: list[dict[str, Any]], mode: str, path: Path) -> None:
    """Persist the rendered report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_report(results, mode), encoding="utf-8")


def _stub_verdict(idx: int) -> str:
    """Deterministic cycling stub: TRUE, FALSE, INCONCLUSIVE."""
    return VERDICTS[idx % 3]


def run_dry(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run the stub evaluator. No network, no LLM, deterministic."""
    results: list[dict[str, Any]] = []
    for i, c in enumerate(claims):
        t0 = time.perf_counter()
        verdict = _stub_verdict(i)
        wall_ms = (time.perf_counter() - t0) * 1000.0
        results.append(
            {
                "id": c["id"],
                "claim": c["claim"],
                "expected": c["expected"],
                "actual_verdict": verdict,
                "confidence": 0.5,
                "rounds": 1,
                "wall_ms": wall_ms,
            }
        )
    return results


async def run_real(
    claims: list[dict[str, Any]],
    *,
    delay_s: float = 0.0,
    retry_errors: bool = True,
) -> list[dict[str, Any]]:  # pragma: no cover
    """Real-mode runner: invokes the live LangGraph against OpenRouter.

    Per-claim failures are caught and recorded as ``actual_verdict="ERROR"``
    so a single rate-limit or network blip doesn't kill the whole batch.
    ``delay_s`` inserts a cooldown between successful claims so upstream
    free-tier rate windows can refill. ``retry_errors`` gives each claim a
    second chance after a longer cooldown before giving up.
    Excluded from coverage — exercised manually before tagging v0.1.0.
    """
    from paper_trail.agents.graph import build_graph
    from paper_trail.agents.state import initial_state

    graph = build_graph()
    results: list[dict[str, Any]] = []

    async def _run_one(claim: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        row: dict[str, Any] = {
            "id": claim["id"],
            "claim": claim["claim"],
            "expected": claim["expected"],
            "actual_verdict": "ERROR",
            "confidence": None,
            "rounds": 0,
            "wall_ms": 0.0,
            "error": None,
        }
        try:
            state = initial_state(claim["claim"], max_rounds=5)
            final = await graph.ainvoke(state)
            row["actual_verdict"] = final.get("verdict") or "INCONCLUSIVE"
            row["confidence"] = final.get("confidence")
            row["rounds"] = final.get("round", 0)
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            row["wall_ms"] = (time.perf_counter() - t0) * 1000.0
        return row

    for idx, c in enumerate(claims):
        row = await _run_one(c)
        if row["actual_verdict"] == "ERROR" and retry_errors:
            print(
                f"[eval] claim {c['id']} failed ({row['error']}); cooldown 60s and retrying once",
                flush=True,
            )
            await asyncio.sleep(60.0)
            retry_row = await _run_one(c)
            # Carry over wall_ms so p95 reflects total work spent on this claim.
            retry_row["wall_ms"] += row["wall_ms"]
            row = retry_row
        if row["actual_verdict"] == "ERROR":
            print(f"[eval] claim {c['id']} failed: {row['error']}", flush=True)
        results.append(row)
        print(
            f"[eval] claim {c['id']}: {row['actual_verdict']} "
            f"(expected {c['expected']}, {row['wall_ms']:.0f}ms)",
            flush=True,
        )
        if delay_s > 0 and idx < len(claims) - 1:
            await asyncio.sleep(delay_s)
    return results


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="evals.run_eval", description="paper-trail eval runner")
    p.add_argument("--dry-run", action="store_true", help="use deterministic stub, no LLM")
    p.add_argument("--max-claims", type=int, default=None, help="cap number of claims")
    p.add_argument(
        "--report-path",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="output markdown path",
    )
    p.add_argument(
        "--claims-path",
        type=Path,
        default=DEFAULT_CLAIMS_PATH,
        help="input claims yaml path",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="cooldown in seconds between successful claims (helps free-tier rate limits)",
    )
    p.add_argument(
        "--no-retry",
        action="store_true",
        help="disable the 60s retry-once on per-claim failures",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    args = _parse_args(argv)
    claims = load_claims(args.claims_path)
    if args.max_claims is not None:
        claims = claims[: args.max_claims]
    if args.dry_run:
        results = run_dry(claims)
        mode = "dry-run"
    else:
        results = asyncio.run(run_real(claims, delay_s=args.delay, retry_errors=not args.no_retry))
        mode = "real"
    write_report(results, mode=mode, path=args.report_path)
    metrics = compute_metrics(results)
    print(
        f"mode={mode} accuracy={metrics['accuracy']:.3f} "
        f"avg_rounds={metrics['avg_rounds']:.2f} p95_ms={metrics['p95_ms']:.1f}"
    )
    if mode == "dry-run":
        return 0
    return 0 if metrics["accuracy"] >= ACCURACY_GATE else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
