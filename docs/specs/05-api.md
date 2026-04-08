# Spec 05 — HTTP + SSE API

## Goal

FastAPI router exposing:

- `POST /debates` — create a debate row, return `{"debate_id": UUID, "stream_url": "/debates/{id}/stream"}`
- `GET /debates/{id}/stream` — Server-Sent Events; one event per node transition + one final event
- `GET /debates/{id}/transcript.md` — plain markdown from `render` node
- `GET /debates/{id}` — JSON snapshot of the debate row
- `GET /debates` — cursor-paginated list

## Invariants

- SSE response carries `Cache-Control: no-cache, no-transform` and `X-Accel-Buffering: no` headers.
- All responses include `X-Request-Id` (propagated from middleware).
- Request bodies validated by `DebateCreateIn` Pydantic model: `claim` ≤2000 chars, non-empty; optional `max_rounds` in `[1,10]` defaulting to 5.
- `GET /debates/{id}` returns 404 on unknown id.
- `GET /debates/{id}/stream` on an unknown id emits a single `{"event":"error","data":{"reason":"not_found"}}` and closes.
- List endpoint returns ≤50 rows per page and a `next_cursor` or `null`.
- Controllers never touch the DB directly; they delegate to `services/debates.py`.

## Test cases (FastAPI `TestClient` + Testcontainers Postgres)

1. `POST /debates` with a valid claim → 201, JSON contains `debate_id` and `stream_url`.
2. `POST /debates` with `claim=""` → 422.
3. `POST /debates` with `claim` over 2000 chars → 422.
4. `POST /debates` with `max_rounds=0` or `11` → 422.
5. `GET /debates/{id}` after create → 200 with `verdict=None` (pending) or final.
6. `GET /debates/not-a-uuid` → 422.
7. `GET /debates/{valid-unknown-uuid}` → 404.
8. `GET /debates/{id}/transcript.md` on a finished debate → 200 `text/markdown` with non-empty body.
9. `GET /debates/{id}/transcript.md` on an unfinished debate → 409 `{"reason":"not_finished"}`.
10. `GET /debates` → first page, assert pagination shape.
11. SSE stream: open, assert ≥1 `event: round` and exactly one `event: final`; cassette-driven LangGraph keeps this deterministic.
12. SSE stream on unknown id → one `event: error` then close (no leaked connection).
13. Every response carries `x-request-id`.
14. CORS: preflight from an allowlisted origin returns 200 with expected headers; non-allowlisted origin returns 400.

## Acceptance

- `api/routers/debates.py` contains only FastAPI glue.
- `services/debates.py` contains orchestration.
- `repositories/debates.py` contains SQLAlchemy queries.
- Tests green against Testcontainers Postgres.
