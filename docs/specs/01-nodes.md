# Spec 01 — LangGraph nodes

## Goal

Five pure async node functions: `plan`, `proponent`, `skeptic`, `judge`, `render`. Each takes a `DebateState` and returns a partial update. All LLM calls go through `core.llm.chat_json` (Judge) or `core.llm.chat` (Proponent/Skeptic/Plan). All web fetches go through `agents.tools.search` and `agents.tools.fetch`.

## Inputs / Outputs

| Node | Reads | Writes |
|---|---|---|
| `plan` | `claim` | `plan: PlanDict` with `sub_questions: list[str]`, `search_queries: list[str]` |
| `proponent` | `claim`, `plan`, `rounds` | appends `{"side": "proponent", "round": N, "argument": str, "evidence": [...]}` |
| `skeptic` | `claim`, `plan`, `rounds` | appends `{"side": "skeptic", "round": N, "argument": str, "evidence": [...]}` |
| `judge` | `claim`, `rounds` | `verdict`, `confidence`, `need_more`, `round +=1` via reducer |
| `render` | full state | `transcript_md: str` |

## Invariants

- Every node is `async`, takes exactly `(state: DebateState)`, returns a partial `dict`.
- Proponent and Skeptic never read each other's round-N output (they run in parallel on round N).
- Judge always emits a verdict ∈ `{"TRUE","FALSE","INCONCLUSIVE"}` plus confidence ∈ `[0,1]`.
- `render` produces deterministic markdown given the same state (stable section order).
- Prompt templates live in `agents/prompts/<name>.md` loaded via `core.prompts.load`.

## Test cases (vcrpy tapes under `tests/fixtures/cassettes/`)

**plan:**
1. Valid claim → returns `plan` with ≥1 `sub_question` and ≥1 `search_query`.
2. Empty string response from LLM → raises `NodeError("plan: empty response")`.
3. Malformed JSON from LLM → raises `NodeError`.

**proponent / skeptic:**
4. With a `plan` containing one query → calls `tools.search` exactly once, `tools.fetch` on each result, returns one round entry with evidence list.
5. Tavily returns empty results → returns argument with `evidence=[]` and an explicit `"no_evidence"` marker.
6. Each respects `round` = current round number and `side` = correct side.
7. Network error on fetch → that single evidence item is marked `{"error": true}` but the node does not crash.

**judge:**
8. Given one pro + one con round → returns valid verdict, confidence ∈ [0,1], `need_more` reflects threshold.
9. Enforces JSON schema on LLM response; non-conforming response → one retry with `temperature=0`, then `NodeError` if still bad.
10. `round = 5, max_rounds = 5` → `need_more = False` regardless of confidence.

**render:**
11. Empty rounds → markdown with header + "No rounds run." placeholder.
12. Multiple rounds → markdown lists each round with pro/con columns and cites every evidence URL.
13. Identical input produces byte-identical output (determinism).

## Acceptance

- Every node has unit test coverage with `vcrpy` for LLM calls and `respx` for HTTP.
- No node touches the database directly.
- `NodeError` is a dedicated exception with `node_name` and `reason` attributes.
