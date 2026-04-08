# Spec 07 — Evals

## Goal

A labeled set of 25 claims with ground-truth verdicts, plus a runner that invokes the graph on each and reports accuracy, average rounds, and p95 latency. The report is committed to `evals/report.md` and is one of the gating artifacts for the Phase 2 acceptance gate.

## Fixtures

- `evals/claims.yaml` — 25 entries shaped `{id, claim, truth: TRUE|FALSE|INCONCLUSIVE, rationale: str, category: str}`. Mix of ~10 true, ~10 false, ~5 inconclusive across science/history/economics.

## Runner

`evals/run_eval.py` — standalone script (not pytest):
1. Load `claims.yaml`.
2. For each claim: call `build_graph().ainvoke(initial_state(claim, max_rounds=5))`.
3. Collect verdict, confidence, rounds run, wall time.
4. Compute accuracy (exact verdict match), avg rounds, p50 / p95 latency.
5. Write `evals/report.md` with a table and summary stats.

## Targets (gate)

- Accuracy ≥ 80% (20/25 or better)
- Average rounds ≤ 3
- p95 latency ≤ 30s
- Zero crashes

## Test cases

1. `load_claims("evals/claims.yaml")` returns exactly 25 typed `Claim` objects.
2. `load_claims` on a malformed YAML raises a clear error.
3. `score_run(claim, result)` returns `True` when verdict matches ground truth.
4. `render_report(results)` emits markdown with accuracy, avg rounds, p95.
5. The report file is deterministic given fixed inputs (so diffs are meaningful).
6. Dry-run mode (`--no-llm`) swaps the graph for a stub that returns a fixed verdict — used in unit tests so CI doesn't spend LLM credits.

## Acceptance

- `evals/claims.yaml` populated with 25 entries, committed.
- `evals/run_eval.py` runs end-to-end against the live OpenRouter stack post-deploy.
- A real run's `evals/report.md` is committed and meets the targets above.
- Unit tests for the runner use the dry-run stub.
