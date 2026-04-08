# Spec 00 — Debate state

## Goal

Define the immutable-per-update LangGraph state object that every node reads from and returns a partial update of. The state is the single source of truth for what has happened in a debate so far: the claim, the plan, the rounds run, the evidence gathered by each side, the Judge's verdicts, and the convergence flag.

## Inputs / Outputs

- **Initial state:** `{"claim": str, "max_rounds": int, "round": 0, "rounds": [], "plan": None, "verdict": None, "confidence": None, "need_more": True}`
- **Per-node partial update:** each node returns only the keys it changed.
- **Reducers:** `rounds` is merged via an append reducer (`operator.add`); every other key is replaced.

## Invariants

- `0 <= round <= max_rounds`
- `len(rounds) == round` after a Judge pass
- `verdict` is `None` or one of `{"TRUE", "FALSE", "INCONCLUSIVE"}`
- `confidence` is `None` or in `[0.0, 1.0]`
- `need_more` is `False` iff `confidence >= CONFIDENCE_THRESHOLD` or `round >= max_rounds`
- `claim` is a non-empty string ≤2000 chars

## Test cases

**Success:**
1. `initial_state("some claim", 5)` returns the canonical shape above.
2. Applying a partial `{"round": 1, "rounds": [<round_dict>]}` to an empty state appends the round.
3. Two sequential partial updates to `rounds` both append (append reducer, not replace).
4. `is_converged(state)` returns `True` when `confidence >= 0.85`.
5. `is_converged(state)` returns `True` when `round >= max_rounds` even at low confidence.

**Failure:**
6. `initial_state("", 5)` raises `ValueError("claim must be non-empty")`.
7. `initial_state("x" * 2001, 5)` raises `ValueError`.
8. `initial_state("ok", 0)` raises `ValueError("max_rounds must be >=1")`.
9. Passing `confidence=1.5` through `validate_state` raises.
10. A reducer that would make `len(rounds) != round` is rejected by `validate_state`.

## Acceptance

- `DebateState` is a `TypedDict` with `Annotated[list, operator.add]` on `rounds` only.
- `initial_state`, `is_converged`, `validate_state` are pure functions with full type hints.
- Every invariant above is enforced by at least one test.
