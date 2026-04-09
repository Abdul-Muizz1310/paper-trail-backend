# paper-trail-backend

> Multi-agent fact-checking debater. Give it a claim — it runs a Proponent vs Skeptic LangGraph with live web evidence, and a Judge scores the verdict over multiple rounds until it converges on `TRUE`, `FALSE`, or `INCONCLUSIVE`.

[![ci](https://github.com/Abdul-Muizz1310/paper-trail-backend/actions/workflows/ci.yml/badge.svg)](https://github.com/Abdul-Muizz1310/paper-trail-backend/actions/workflows/ci.yml)
[![deployed on render](https://img.shields.io/badge/deploy-render-%2346e3b7)](https://paper-trail-backend-7h27.onrender.com/health)
[![coverage](https://img.shields.io/badge/coverage-93%25-brightgreen)](#testing)

**Live:** https://paper-trail-backend-7h27.onrender.com · **Docs:** [`/docs`](https://paper-trail-backend-7h27.onrender.com/docs) · **Spec:** [`docs/specs/`](docs/specs/) · **Demo walkthrough:** [`docs/DEMO.md`](docs/DEMO.md)

## Why this exists

Most "ChatGPT with citations" demos make one LLM call, paste a few footnotes after the fact, and call it done. paper-trail runs a **debate** instead: a Proponent and a Skeptic each build an evidence-backed argument, and a Judge scores who actually held up. The transcript is the product — you read *why* the verdict landed where it did, not just a number.

The gate to ship was concrete: ≥80% accuracy on a labelled 25-claim eval set. The [latest run](evals/report.md) got **21/25 = 84%**, avg 1.3 rounds, p95 45.7s.

## What the API does

| Endpoint | Purpose |
|---|---|
| `POST /debates` | Create a debate. Kicks off the graph as a background task. Returns `{debate_id, stream_url}`. |
| `GET /debates/{id}` | Fetch current state (status, verdict, confidence, rounds). |
| `GET /debates/{id}/stream` | Server-Sent Events. Emits a `state` event per round and a `done` event at terminal state. |
| `GET /debates/{id}/transcript.md` | Deterministic markdown transcript with inline citations. |
| `GET /debates` | Cursor-paginated list. |
| `POST /platform/debate` | Synchronous bearer-authenticated endpoint for bastion integration. Caps `max_rounds` at 3 for latency. |
| `GET /health` | Liveness probe. |
| `GET /docs` | OpenAPI UI. |

## Architecture

```
HTTP
 └─ services/DebateService
     ├─ repositories/DebateRepo  ← async SQLAlchemy + pgvector on Neon
     └─ agents/graph.build_graph()
         ├─ plan          (chat_json + Tavily search)
         ├─ proponent ‖ skeptic   (parallel chat)   ← cycle target
         ├─ judge         (chat_json) ──needs_more──┐
         └─ render        (pure markdown)           │
                                                    └──→ proponent ‖ skeptic
```

- **LangGraph cyclic state machine** — the `judge → proponent/skeptic` back-edge is a real `add_conditional_edges` call in [`agents/graph.py`](src/paper_trail/agents/graph.py). Convergence: `confidence >= 0.85 || round >= max_rounds`.
- **MVC layering** — routers → services → repositories → models. No layer reaches across.
- **Typed everything** — Pydantic v2 DTOs at the HTTP boundary, TypedDict state in the graph, strict mypy.
- **Pure core, imperative shell** — node bodies are async pure functions of state; all I/O (LLM, Tavily, DB, LangFuse) lives at the edges.

## Observability

Every debate emits a full OpenTelemetry-backed trace to LangFuse:

```
debate.run  (trace: tags=[verdict, model, env], session_id=debate_id, input/output)
├─ node.plan
│  ├─ llm.json   (generation: messages, completion, tokens)
│  └─ tool.search × N
├─ node.proponent ‖ node.skeptic
│  └─ llm.chat
├─ node.judge
│  └─ llm.json
└─ node.render
```

Per-query tool failures are surfaced in `node.plan` span metadata (`failed_query_count`, `failed_queries`) instead of being silently swallowed. Tracing never fails a request — the wrapper degrades to no-op on any LangFuse-side error.

## Stack

- **FastAPI** + `sse-starlette` for the HTTP surface
- **LangGraph** for the debate state machine (async, cyclic)
- **async SQLAlchemy + asyncpg** on **Neon Postgres** (pgvector extension ready for the memory tool)
- **Alembic** migrations, run via `preDeployCommand` on Render
- **OpenRouter** via `httpx` — primary/fallback cascade with jittered exponential backoff on 429
- **Tavily** web search
- **LangFuse v3** tracing (OTel spans, not the legacy v2 API)
- **pytest-asyncio** + **Polyfactory** + in-memory sqlite for the unit suite

Default models via OpenRouter (BYOK for Google AI Studio in production):

- Primary: `google/gemini-2.0-flash-001`
- Fast: `google/gemini-2.5-flash-lite`
- Fallback: `google/gemini-2.5-flash-lite`

## Run locally

```bash
cp .env.example .env
# fill: DATABASE_URL, OPENROUTER_API_KEY, TAVILY_API_KEY, LANGFUSE_*, UPSTASH_REDIS_REST_*
uv sync --all-extras
uv run alembic upgrade head
uv run uvicorn paper_trail.main:app --reload
```

Quick end-to-end via the synchronous platform endpoint:

```bash
curl -X POST http://localhost:8000/platform/debate \
  -H 'Authorization: Bearer demo' \
  -H 'Content-Type: application/json' \
  -d '{"claim":"Humans only use 10 percent of their brains.","max_rounds":2}'
```

Expected: `{"verdict":"FALSE","confidence":0.95,...}` in ~30–60s.

Async/streaming path (matches the web UI flow):

```bash
# create
curl -X POST http://localhost:8000/debates \
  -H 'Content-Type: application/json' \
  -d '{"claim":"The Great Wall is visible from low Earth orbit.","max_rounds":3}'
# → {"debate_id":"...","stream_url":"/debates/<id>/stream"}

# subscribe to the stream
curl -N http://localhost:8000/debates/<id>/stream
```

## Testing

```bash
uv run pytest                                     # full suite
uv run pytest -m "not slow and not integration"   # fast-only
uv run pytest --cov=src/paper_trail --cov-report=term-missing
```

- **100 unit tests**, 93% coverage
- **Red-first TDD** — every feature has a failing test committed before implementation (`test: red tests for <feature>` pattern in the git log)
- No real LLM / Tavily / LangFuse calls in tests — all external I/O is mocked with `respx` or dependency-overridden in-memory fakes

## Running the eval gate

```bash
uv run python -m evals.run_eval --dry-run       # 25 claims, stub verdicts (CI-safe)
uv run python -m evals.run_eval --delay 10      # 25 claims, real LLM, report to evals/report.md
uv run python -m evals.run_eval --max-claims 5  # smoke run
```

Real mode exits non-zero if accuracy < 80%. See [`evals/report.md`](evals/report.md) for the latest numbers.

## Deploy

Render free tier via [`render.yaml`](render.yaml). One-time setup:

1. Render dashboard → **New → Blueprint** → connect this repo
2. Fill every `sync: false` env var in the service settings
3. Copy the Deploy Hook URL → `gh secret set RENDER_DEPLOY_HOOK --repo <you>/paper-trail-backend --body '<url>'`
4. Push to `main` → CI lint/test/build → CI fires the hook → Render rebuilds → `preDeployCommand: alembic upgrade head` → new container goes live

## License

MIT. See [LICENSE](LICENSE).
