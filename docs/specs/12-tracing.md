# Spec 12 — Langfuse tracing (Langfuse v4, Python SDK v5)

## Goal

Every debate is one Langfuse trace whose tree reads like the debate ran: who argued, which model
answered each call, what it cost in tokens, which tool failed, and where the time went. The
target is the Langfuse v4 platform (observations-first; Langfuse Cloud is v4-only from
2026-11-16) through **Python SDK v5** (`langfuse>=5.0.4,<6`), the SDK Langfuse names for Cloud.

This replaces the wrapper written for SDK v3 (`core/langfuse.py`). Pulled back from the project on
2026-10-26, its traces had no model and no token usage on any generation (both went to metadata),
a session id on the root only, failed calls at level `DEFAULT`, no tags and environment `default`;
it flushed synchronously on every span exit, on the event loop; and on SDK v5 its trace-attribute
helper raises `AttributeError` (`TRACE_INPUT` is gone) inside a `try/except` that hides it — the
failure mode of `lesson-observability-second`.

## Client (`core/tracing.py`)

- Built once, on first use, from the settings: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`,
  `LANGFUSE_BASE_URL` (US Cloud: `https://us.cloud.langfuse.com`). **Both keys** set → tracing is
  on. Either blank → the client is built with `tracing_enabled=False`: every observation is a
  no-op, nothing is exported, nothing touches the network.
- **Environment** = `APP_ENV` (`production` on Render, `development` locally, `test` in the
  suite). `APP_ENV` must be a valid Langfuse environment, `^(?!langfuse)[a-z0-9_-]{1,40}$`: the
  settings refuse anything else at startup instead of the SDK dropping it later.
- **Release** is left to the SDK, which reads `RENDER_GIT_COMMIT` on Render.
- **Export** runs in the SDK's background batches. Nothing on the request path flushes.
  `shutdown()` (flush + stop) runs when the app shuts down (FastAPI lifespan) and at the end of
  `evals/run_eval.py`, a short-lived process.
- Instrumentation is manual. The LangChain `CallbackHandler` was tried and rejected: with our raw
  `httpx` model calls inside the nodes, its node spans did not become the parent of those
  generations (they attached to the root instead).

## Trace shape

One trace per `DebateService.run`. Names are stable and verb-first (evaluators, dashboards and
saved filters target them); run-specific values live in metadata, never in a name.

| Step | Type | Name | Input | Output |
|---|---|---|---|---|
| the debate | `agent` | `run-debate` | `{claim, max_rounds}` | `{verdict, confidence, rounds_run}` |
| plan node | `agent` | `plan-debate` | `{claim, pool_size}` | `{sub_questions, search_queries, evidence_count, fetched_article_count, failed_query_count}` |
| proponent node | `agent` | `argue-for` | `{claim, round, evidence_count, pool_size}` | `{round, argument, citation_count}` |
| skeptic node | `agent` | `argue-against` | `{claim, round, evidence_count, pool_size}` | `{round, argument, citation_count}` |
| judge node | `agent` | `judge-round` | `{claim, round_just_completed, max_rounds, rounds_accumulated}` | `{verdict, confidence, reasoning, need_more, next_round}` |
| render node | `chain` | `render-transcript` | `{claim, rounds_count, verdict}` | `{transcript_length, rounds_struct_count, transcript_hash}` |
| Tavily search | `tool` | `search-web` | `{query, k}` | `{hit_count, titles}` |
| article fetch | `tool` | `fetch-article` | `{url}` | `{char_count}` |
| each model attempt | `generation` | `decompose-claim` (plan), `write-argument` (proponent, skeptic), `score-round` (judge) | the chat messages | `{role: "assistant", content[, reasoning]}` |

Each step nests under the one that ran it: tools and generations under their node, nodes under
`run-debate`, also across the proponent/skeptic `Send` branches that run in parallel.

### Generations

`core/llm.chat` / `chat_json` take the generation's `name`. Every HTTP attempt of the cascade is
its own generation (never one generation wrapping the cascade):

- `model`: the slug that answered (OpenRouter's `model`, else the requested slug).
- `model_parameters`: `temperature`; `response_format: "json_object"` in JSON mode.
- `metadata`: `tier` (`primary|fast|fallback`), `attempt` (1-based within the tier), `provider`
  (OpenRouter's upstream provider, when reported).
- `usage_details`, from OpenRouter's `usage`, as **exclusive** buckets (Langfuse adds buckets, so
  an inclusive count would be double-counted):
  `input = prompt_tokens − cached_tokens`, `input_cached_tokens = cached_tokens` (when > 0),
  `output = completion_tokens − reasoning_tokens`, `output_reasoning_tokens = reasoning_tokens`
  (when > 0). A missing or non-integer count drops its bucket; no bucket is ever negative.
- `cost_details = {"total": usage.cost}` when OpenRouter reports a number (`0` on `:free` models).
- `output`: the assistant message, with OpenRouter's `reasoning` text when the model returned one.
- A failed attempt is level `ERROR` with `status_message` `"<stage>: <detail>"` (for example
  `rate_limited: status=429`), and the `LLMError` still reaches the cascade unchanged. An attempt
  cut off by its tier's time share or the debate deadline is `ERROR` too (`cancelled before the
  model answered`), not left looking like a success.

### Trace attributes

Set with `propagate_attributes` on entering `run-debate`, so every observation carries them:

- `trace_name`: `run-debate`.
- `session_id`: the request's `X-Request-Id` as slowquery-detective validated it
  (`current_request_id()`, which background tasks inherit). bastion sends one id for every call of
  a dossier, so a dossier's traces across products group into one session. No id → no session.
- `tags`: `["paper-trail", "entry:<api|platform|eval>"]` — the service (all products share one
  Langfuse project) and what started the debate: `POST /debates`, `POST /platform/debate`, or
  `evals/run_eval.py`.
- `metadata`: `{"debate_id": "<uuid>"}`.

Tags are immutable in Langfuse v4 and set at creation, so nothing learned later (the verdict, an
error) becomes a tag; the verdict is the root's output and an error is the root's level.

## Failure behaviour

- A node, tool or model attempt that raises: its observation is `ERROR` (the SDK records the
  exception) and the exception propagates unchanged.
- `plan-debate` is `WARNING` when some of its searches or fetches failed but the debate goes on,
  with `status_message` `"<n> of <m> searches or fetches failed"`; the failures stay in its
  `failed_queries` metadata.
- A debate that fails or passes `debate_deadline_s`: `run-debate` is `ERROR`; the service still
  marks the debate `error` and re-raises.
- Tracing never changes a debate's outcome: an exporter that fails leaves the debate's result,
  rows and return value as they would be without tracing.

## Test cases

Fast tier (`tests/unit/test_tracing.py`, a client on an in-memory exporter — no network):

- **T1** keys set → an enabled client with the configured base URL and `APP_ENV` as environment;
  either key blank → tracing disabled, and a full debate exports nothing.
- **T2** `APP_ENV`: `production`, `test`, `dev_1`, `pr-12` accepted; `Production`, `langfuse-ci`,
  `""` and a 41-character value rejected at settings load.
- **T3** `usage_details`: no `usage` → `{}`; `{prompt_tokens: 10, completion_tokens: 4}` →
  `{input: 10, output: 4}`; with `cached_tokens: 6` → `{input: 4, input_cached_tokens: 6, …}`; with
  `reasoning_tokens: 3` → `{…, output: 1, output_reasoning_tokens: 3}`; zero details add no
  bucket; a string, a float, a negative count or details larger than their total never yield a
  negative or non-integer bucket.
- **T4** `cost_details`: `cost: 0` → `{total: 0.0}`; `cost: 0.0012` → `{total: 0.0012}`; absent,
  `null` or a string → `None`.
- **T5** one `chat_json` call answered by the primary → one `generation` named by the caller, with
  the model, `model_parameters`, the messages as input, the assistant message as output, usage,
  cost and `tier`/`attempt`/`provider` metadata.
- **T6** a 429 then a 200 on the primary → two generations: the first `ERROR` with
  `rate_limited: status=429`, the second `DEFAULT`.
- **T7** every tier failing → one `ERROR` generation per attempt; `LLMError("all_models_exhausted")`
  still raised. Every tier hanging past a 0.3 s node deadline → one generation per tier, each
  `ERROR` with `cancelled before the model answered`.
- **T8** a reply with `reasoning` → the output message carries it; a 200 whose completion is
  `null` or blank → `bad_response`, the next tier is asked (spec 04 rule 3).
- **T9** `search-web`: a `tool` with `{query, k}` in and `{hit_count, titles}` out; a Tavily 500 →
  `ERROR`, `ToolError` raised.
- **T10** `fetch-article`: a `tool` with `{url}` in and `{char_count}` out; a failed fetch →
  `ERROR`, `ToolError` raised. A plan whose only search fails → `plan-debate` is `WARNING` with
  `1 of 1 searches or fetches failed`.
- **T11** a whole debate (graph run, HTTP stubbed with respx) under `X-Request-Id`-style request id
  `dossier-7`: one trace; root `run-debate` (`agent`) with its input and output; children
  `plan-debate`, `argue-for`, `argue-against`, `judge-round`, `render-transcript`, each model call
  nested under its node; every observation has trace name `run-debate`, tags `paper-trail` and
  `entry:api`, environment `test`, `debate_id` metadata and session `dossier-7`.
- **T12** the same debate with no request id → no observation has a session.
- **T13** a debate whose judge raises → `run-debate` is `ERROR`, the debate is stored as `error`,
  the exception is re-raised.
- **T14** an exporter that raises on every export → the debate completes with its verdict.
- **T15** app shutdown (lifespan exit) calls `tracing.shutdown()` exactly once.

## Acceptance

- `langfuse>=5.0.4,<6` with mypy checking our calls against the SDK's own types (`langfuse.*` is
  not in the `ignore_missing_imports` list).
- No `core/langfuse.py`, no `update_current_trace`, no per-span `flush()`.
- Every case above is a test; coverage of `core/tracing.py` is 100%.
