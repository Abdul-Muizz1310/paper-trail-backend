# Spec 08 — Evidence Pool + Structured Transcript (Block 6)

## Goal

1. Extend `POST /debates` with an optional `evidence_pool`: a caller-supplied
   collection of pre-collected source material (e.g. inkprint certificates)
   that the graph **prefers** over a fresh Tavily search.
2. Add `GET /debates/{id}/transcript.json` — a structured, machine-readable
   transcript that pairs each round with typed citations and carries a
   deterministic `transcript_hash` for integrity checks.

The stream / SSE endpoint is untouched; `transcript.md` is untouched; all
existing behavior remains intact when `evidence_pool` is absent or empty.

## Behavior

### 1. `POST /debates` request schema extension

```json
{
  "claim": "string (1..2000)",
  "max_rounds": 5,
  "evidence_pool": [
    {
      "certificate_id": "UUID",
      "url": "string",
      "title": "string",
      "text": "string (content)"
    }
  ]
}
```

- `evidence_pool` is optional. If omitted or provided as an empty list, the
  persisted column is `NULL` and the graph runs with existing Tavily-only
  behavior (backward compatible).
- Length must be `0..50`. `len > 50` is rejected with 422.
- Each item is frozen (`extra=forbid`). Missing fields or an invalid
  `certificate_id` UUID → 422.

When `evidence_pool` is non-empty:

- **Plan node** instructs the planner LLM to "draft sub-questions that can
  be answered from the following pre-collected evidence: <serialized pool>".
  Tavily is still called to *augment* the pool for sub-questions the pool
  cannot answer.
- **Proponent / Skeptic** include the pool in their user-message context.
  When the LLM cites a pool item by `certificate_id` (via the literal
  `[cert:<uuid>]` marker), the round entry's `citations` list gets a
  `{"type": "cert", "ref": "<uuid>", "title": "<pool title>"}` entry.
  For Tavily-sourced URLs, it gets `{"type": "url", "ref": "<url>",
  "title": "<hit title>"}`.
- **Judge** logic is unchanged — citations flow through unmodified on each
  round entry.
- **Render** node now emits two outputs: the existing markdown transcript
  and structured `rounds_struct` with per-round citations.

### 2. `GET /debates/{id}/transcript.json`

- `200` when the debate has status `done`:

  ```json
  {
    "debate_id": "UUID",
    "claim": "string",
    "verdict": "TRUE|FALSE|INCONCLUSIVE",
    "confidence": 0.0,
    "rounds": [
      {
        "side": "proponent|skeptic|judge",
        "round": 1,
        "argument_md": "string",
        "citations": [
          {"type": "cert|url", "ref": "string", "title": "string"}
        ],
        "confidence": 0.0
      }
    ],
    "transcript_hash": "64-char hex sha256"
  }
  ```

- `404` when the debate id does not exist.
- `409 {"detail": "Debate still running"}` when status is anything other than
  `done` (including `pending`, `running`, `error`).

- `transcript_hash` = SHA-256 over canonical JSON of
  `{claim, verdict, confidence, rounds}` (sorted keys, no whitespace,
  `ensure_ascii=False`). Deterministic: identical inputs → identical hash.

### 3. Persistence

- `debates.evidence_pool JSONB NULL` — stores the frozen list (or null).
- `debates.transcript_hash TEXT NULL` — populated when render completes.
- `debates.rounds_struct JSONB NULL` — structured rounds produced by render.

## Negative space

- `POST /debates` with `evidence_pool=[]` is equivalent to absent — stored
  as NULL (parse, don't validate).
- Duplicate `certificate_id` entries are allowed (caller contract, not our
  invariant).
- A `[cert:<uuid>]` marker in LLM output that does **not** match any pool
  `certificate_id` is dropped from the citations list (we never invent
  cert refs).
- `transcript_hash` is only populated for completed debates. In-progress
  debates have NULL. `transcript.json` returns 409 for non-`done` status.

## Test cases (in order)

### API-level

1. `POST /debates` with only `claim` → 200, evidence_pool null.
2. `POST /debates` with claim + 3-item pool → 201, pool persisted.
3. `POST /debates` with `evidence_pool=[]` → 201, null persisted.
4. `GET /debates/{id}/transcript.json` on completed debate → 200 shape.
5. `transcript_hash` deterministic across calls.
6. Rounds include citations when pool used.

### Failure

7. `GET transcript.json` on running → 409.
8. `GET transcript.json` on unknown id → 404.
9. `POST` with 51-item pool → 422.
10. `POST` with non-UUID certificate_id → 422.
11. `POST` with pool item missing `text` → 422.

### Pure

12. `canonical_transcript_json` deterministic for same rounds.
13. `extract_cert_markers` parser returns cert_ids from `[cert:<uuid>]`.

### Node

14. Plan prompt includes pool when present.
15. Plan prompt omits pool when absent.
16. Proponent citations contain only pool cert_ids (never invented URLs).
17. Skeptic citations contain only pool cert_ids.
18. Judge preserves citations unchanged.

### Integration

19. End-to-end run with 3-item pool: transcript has cert citations.
20. End-to-end run with no pool: URLs only, no `[cert:...]` markers.
21. Transcript hash stable across repeated render calls.

## Acceptance

- `schemas/debates.py` exposes `EvidencePoolItem` + `DebateCreateIn.evidence_pool`.
- `agents/nodes/render.py` emits `transcript_md`, `rounds_struct`, and
  `transcript_hash`.
- `api/routers/debates.py` adds `GET /debates/{id}/transcript.json`.
- Alembic migration `0002_evidence_pool.py` adds three nullable columns.
- All 21 enumerated test cases green. Coverage ≥ baseline (99%).
- `ruff check` + `mypy --strict` clean.
