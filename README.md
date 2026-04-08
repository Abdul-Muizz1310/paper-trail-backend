# paper-trail-backend

> Multi-agent debater with cited evidence. Give it a claim, it runs a Proponent vs Skeptic LangGraph, and a Judge scores the verdict over multiple rounds.

[![ci](https://github.com/Abdul-Muizz1310/paper-trail-backend/actions/workflows/ci.yml/badge.svg)](https://github.com/Abdul-Muizz1310/paper-trail-backend/actions/workflows/ci.yml)

## What it does

- `POST /debates` → creates a debate, returns an SSE stream URL
- `GET /debates/{id}/stream` → Server-Sent Events with each node's output as it happens
- `GET /debates/{id}/transcript.md` → final markdown transcript with inline citations
- `POST /platform/debate` → integration endpoint invoked by the bastion control plane (bounded to 3 rounds)
- `/health`, `/version`, `/metrics` — platform probes

## Stack

FastAPI · LangGraph · SQLAlchemy async + pgvector on Neon · OpenRouter (Qwen/Gemma free-tier) · Tavily · Voyage AI embeddings · LangFuse traces · Upstash Redis cache.

## Run locally

```bash
cp .env.example .env
# fill in OPENROUTER_API_KEY, TAVILY_API_KEY, VOYAGE_API_KEY, DATABASE_URL, ...
uv sync --all-extras
uv run alembic upgrade head
uv run uvicorn paper_trail.main:app --reload
```

Then:

```bash
curl -X POST http://localhost:8000/debates \
  -H 'content-type: application/json' \
  -d '{"claim": "Regular multivitamin use reduces all-cause mortality in healthy adults."}'
```

## Testing

```bash
uv run pytest                             # unit + integration
uv run pytest -m "not slow"               # skip end-to-end LLM tape replays
uv run pytest --cov=src --cov-report=term-missing
```

Integration tests use Testcontainers to spin a real Postgres+pgvector. LLM calls are replayed from VCR tapes under `tests/fixtures/cassettes/`.

## Deploy

Render free tier via `render.yaml`. First-time setup: dashboard → New → Blueprint → connect repo. After provision, copy the Deploy Hook URL into repo secrets as `RENDER_DEPLOY_HOOK`. CI hits the hook on push to `main`.

## License

MIT. See [LICENSE](LICENSE).
