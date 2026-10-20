# Spec 11 — Query observability (slowquery-detective 0.2)

## Goal

bastion's dossier "measure" stage asks every service it called which queries a request caused:
`GET /_slowquery/queries?request_id=R`. paper-trail answers it with slowquery-detective 0.2.0
(interface I-2 of the bastion v2 rollout plan, the library's `docs/specs/07-fleet-observability.md`).
A debate's work happens in a background task after `POST /debates` has answered, so the case that
matters is that those queries are still counted against the request that started the debate.

## Wiring (`platform/query_observability.py`, called from `main.create_app`)

`install(app, engine, service="paper-trail", store_url=…, authorize=…)`, with:

- **engine**: the app's own `AsyncEngine` (`core.db.make_engine()`, memoised; creating it opens no
  connection), so it exists at construction time;
- **store_url**: the service's own `DATABASE_URL` when it is Postgres (`postgres://`,
  `postgresql://`, `postgresql+<driver>://`); the library keeps its tables in schema `slowquery`,
  created idempotently at startup. Any other URL (the fast tier's SQLite) → `None`, the in-memory
  store;
- **authorize**: `True` only for a request the contract-v2 middleware authenticated with bastion's
  key (`principal.iss == "bastion"`). A frontend-key principal or an anonymous request → `False`,
  which the library answers with 403;
- **order**: `install` is the last middleware call in `create_app`, so its request-id tagger is the
  outermost layer and every statement the request causes, in the token check, the endpoint and its
  background tasks, runs inside it.

The routes stay behind the platform token middleware (they are not on the public list): no token →
401 `missing_platform_token`, a frontend token → 403 from `authorize`, a bastion token → 200.

`X-Request-Id` is read by the library (1–128 of `[A-Za-z0-9._:-]`); our request-id middleware still
echoes or mints an id, but a request without the header is not attributed.

## Test cases

Fast tier (`tests/unit/test_query_observability.py`, SQLite, in-memory store):

- **O1** `create_app()` installs the library once: `app.state.slowquery_installed`, one tagger, and
  it is the outermost user middleware (the routes answer in O4).
- **O2** `store_url_for`: `postgres://`, `postgresql://`, `postgresql+asyncpg://…?ssl=require` → the
  URL unchanged (the library normalises it); `sqlite+aiosqlite:///:memory:`, `""` → `None`.
- **O3** `authorize_bastion`: a bastion principal → `True`; a frontend principal (`iss
  "paper-trail-frontend"`) → `False`; no principal → `False`.
- **O4** composed over ASGI with the lifespan running and both keys configured: `GET
  /_slowquery/queries` without a token → 401 missing; with a frontend token → 403; with a bastion
  token → 200.

Integration tier (`tests/integration/test_query_observability_pg.py`, Testcontainers Postgres,
`store_url` = the container):

- **O5** a `POST /debates` carrying `X-Request-Id: R` and a bastion token runs the real
  `DebateService` (the graph stubbed, no LLM): `GET /_slowquery/queries?request_id=R` with a bastion
  token lists the endpoint's `INSERT INTO debates` **and** the background task's `UPDATE debates`
  statements; a second, untagged debate adds nothing to R; the store's own `slowquery.` statements
  are never listed.
- **O6** the same route without a token → 401, with a frontend token → 403.
