# Spec 06 — Persistence

> **Scope note (kept honest).** `debate_embeddings` was **descoped before v0.1
> shipped and does not exist** — no migration creates it, no model maps it, and
> nothing in `src/` computes an embedding. Cases 9–10 below are struck for the
> same reason. What ships is a single `debates` table
> (`alembic/versions/0001_initial_debate_table.py`, extended by
> `0002_evidence_pool.py`).

## Goal

Async SQLAlchemy 2.0 models, repositories, and Alembic migrations for `debates` on Neon Postgres.

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

Added later by `0002_evidence_pool.py` (all nullable, for backward compatibility
with rows created before it):
- `evidence_pool JSON NULL` — caller-supplied evidence (spec 08)
- `rounds_struct JSON NULL` — typed rounds + citations backing `transcript.json`
- `transcript_hash TEXT NULL` — SHA-256 over the canonical transcript

**debate_embeddings — DESCOPED, does not exist:**
- ~~`id UUID PK`~~
- ~~`debate_id UUID FK debates(id) ON DELETE CASCADE`~~
- ~~`text TEXT NOT NULL`~~
- ~~`embedding VECTOR(1024) NOT NULL`~~
- ~~`created_at TIMESTAMPTZ NOT NULL DEFAULT now()`~~
- ~~Index: `ivfflat (embedding vector_cosine_ops) WITH (lists=100)`~~

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
9. ~~Inserting into `debate_embeddings` with a 1024-dim vector succeeds.~~ **DESCOPED.**
10. ~~Cosine similarity query returns nearest neighbor first.~~ **DESCOPED.**
11. A committed write from one session is visible to a second, independent session
    against a real Postgres (`tests/integration/test_pg_concurrent_sessions.py`).

## Deploy-time migrations (2026-10-20)

`render.yaml` ran `alembic upgrade head` as a `preDeployCommand`, but the service is on Render's free
plan, which does not run pre-deploy commands (they need a paid instance), so no deploy ever applied
a migration and the README's "auto-applied" claim was false. The container itself now applies them,
mirroring inkprint-backend:

- `docker-entrypoint.sh` is the image's `ENTRYPOINT`; the `CMD` (uvicorn, exec form) is unchanged
  and the entrypoint `exec`s it, so uvicorn stays PID 1 and receives Render's signals.
- With a Postgres `DATABASE_URL` (`postgres://`, `postgresql://`, `postgresql+<driver>://`) it runs
  `alembic upgrade head` first. Alembic records the applied revision in `alembic_version`, so the
  step runs on every boot and is a no-op at head (review focus: a migration that runs twice).
- Any other `DATABASE_URL`, or none, skips Alembic: the migrations are Postgres-targeted, and the
  fast tier's SQLite never runs them.
- A failed migration aborts the boot (`set -e`) and the CMD never starts: Render keeps the previous
  deploy serving instead of a new one running against an un-migrated schema.
- `render.yaml` carries no `preDeployCommand` while `plan: free`.

Cases:

- **M1** (`tests/unit/test_deploy_entrypoint.py`, a stub `alembic` on `PATH`) a
  `postgresql+asyncpg://` URL runs `alembic upgrade head`, then the CMD; so do `postgres://` and
  `postgresql://`.
- **M2** an unset, empty or `sqlite+aiosqlite://` URL skips Alembic and still runs the CMD.
- **M3** a failing `alembic` exits non-zero and the CMD never runs.
- **M4** the CMD's arguments reach it unchanged (`exec "$@"`).
- **M5** the Dockerfile copies the entrypoint and declares it as `ENTRYPOINT` with an exec-form
  `CMD`; `render.yaml` on `plan: free` has no `preDeployCommand`.
- **M6** (`tests/integration/test_migrations_pg.py`, Testcontainers `pgvector/pgvector:pg16`)
  `alembic upgrade head` against an empty Postgres creates `debates` with every model column and
  stamps the head revision; a second run succeeds and changes nothing.

## Acceptance

- Initial Alembic migration `0001_initial` under `alembic/versions/`.
- Enables the `vector` extension via `CREATE EXTENSION IF NOT EXISTS vector` —
  groundwork only; no column uses a vector type.
- Repositories never touch HTTP or LLM modules.
