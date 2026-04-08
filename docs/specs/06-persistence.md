# Spec 06 — Persistence

## Goal

Async SQLAlchemy 2.0 models, repositories, and Alembic migrations for `debates` and `debate_embeddings` on Neon Postgres with pgvector.

## Tables

**debates**
- `id UUID PK` (`uuid_generate_v4`)
- `claim TEXT NOT NULL`
- `max_rounds INT NOT NULL DEFAULT 5`
- `verdict VARCHAR(20) NULL` — `TRUE|FALSE|INCONCLUSIVE`
- `confidence DOUBLE PRECISION NULL`
- `rounds JSONB NOT NULL DEFAULT '[]'`
- `transcript_md TEXT NULL`
- `status VARCHAR(20) NOT NULL DEFAULT 'pending'` — `pending|running|done|error`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`

**debate_embeddings**
- `id UUID PK`
- `debate_id UUID FK debates(id) ON DELETE CASCADE`
- `text TEXT NOT NULL`
- `embedding VECTOR(1024) NOT NULL`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- Index: `ivfflat (embedding vector_cosine_ops) WITH (lists=100)`

## Repository API

```python
# repositories/debates.py
class DebateRepo:
    async def create(self, claim: str, max_rounds: int) -> Debate
    async def get(self, debate_id: UUID) -> Debate | None
    async def list(self, cursor: str | None, limit: int = 50) -> tuple[list[Debate], str | None]
    async def update_result(self, debate_id: UUID, verdict: str, confidence: float, rounds: list[dict], transcript_md: str) -> None
    async def set_status(self, debate_id: UUID, status: str) -> None
```

## Invariants

- Every write uses a transaction committed via `async with session.begin()`.
- Cursor pagination is stable: uses `(created_at, id)` tuple encoded base64.
- Status transitions are monotonic: `pending → running → (done|error)`.

## Test cases (Testcontainers Postgres with pgvector)

1. `create` inserts a row with a new UUID, returns populated `Debate`.
2. `get(existing)` returns the row; `get(unknown)` returns `None`.
3. `list` returns rows newest-first; `next_cursor` non-null when more rows exist.
4. `list(cursor=<from previous>)` returns strictly older rows.
5. `update_result` sets verdict/confidence/rounds/transcript and flips status to `done`.
6. Calling `update_result` on an unknown id raises.
7. `set_status("error")` transitions a `running` row.
8. `set_status("running")` on a `done` row raises (monotonic guard).
9. Inserting into `debate_embeddings` with a 1024-dim vector succeeds.
10. Cosine similarity query returns nearest neighbor first.

## Acceptance

- Initial Alembic migration `0001_initial` under `alembic/versions/`.
- Enables `vector` extension via `CREATE EXTENSION IF NOT EXISTS vector`.
- Repositories never touch HTTP or LLM modules.
