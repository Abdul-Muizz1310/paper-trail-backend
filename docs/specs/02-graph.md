# Spec 02 — LangGraph assembly

## Goal

Wire the five nodes into a `StateGraph` with a parallel fan-out from `plan` to both `proponent` and `skeptic`, a fan-in to `judge`, and a conditional back-edge from `judge` to `plan` (for another round) or to `render` (converged).

## Topology

```
START → plan → proponent ─┐
              ↘ skeptic ──┤→ judge ─?─→ plan  (need_more)
                          │           ↘ render → END  (converged)
```

## Invariants

- Graph compiles without errors.
- Proponent and Skeptic execute concurrently within a single super-step (LangGraph parallel edges).
- The conditional edge out of `judge` uses `is_converged(state)` from spec 00.
- Cyclic execution is observable: running a pinned-LLM tape that yields `confidence < 0.85` on round 1 triggers a second round.
- `.ainvoke(initial_state)` always terminates (bounded by `max_rounds`).

## Test cases

1. `build_graph().compile()` returns a runnable.
2. Async invoke on a claim that converges in round 1 → `len(rounds) == 2` (one pro, one con), `verdict != None`.
3. Async invoke on a claim that needs 3 rounds (pinned tape) → `len(rounds) == 6`, round counter reaches 3.
4. `max_rounds=1` → graph always terminates in one round regardless of confidence.
5. Running Proponent+Skeptic in parallel is observable via timing: total wall time < sum of each (mock nodes with `asyncio.sleep(0.5)` each → total ≤ 0.7s).
6. A node raising `NodeError` propagates out of `ainvoke` (no silent swallow).
7. Graph is deterministic with pinned LLM tapes: two runs produce identical `transcript_md`.

## Acceptance

- `agents/graph.py` exposes `build_graph()` returning the compiled graph.
- Parallelism verified by a timing test using mocked node sleeps.
- Cyclic back-edge verified by a multi-round pinned-tape test.
