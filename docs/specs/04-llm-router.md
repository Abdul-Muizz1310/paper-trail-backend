# Spec 04 — LLM router

## Goal

A thin wrapper around `langchain-openai` pointed at OpenRouter that exposes `chat(messages, **kwargs)` and `chat_json(messages, schema)`. Implements primary-with-fallback on rate-limit / server errors, enforces JSON mode for structured outputs, and enforces a hard per-call timeout.

## Public API

```python
# core/llm.py
async def chat(messages: list[ChatMessage], *, model: ModelTier = "primary", temperature: float = 0.2) -> str
async def chat_json(messages: list[ChatMessage], schema: type[BaseModel], *, model: ModelTier = "primary") -> BaseModel
```

`ModelTier = Literal["primary", "fast", "fallback"]` resolves to the env-driven `OPENROUTER_MODEL_*` slugs.

### Default slugs (2026-10-26)

The env vars win; the code defaults apply only when a slot's variable is unset. Every default is a
`:free` OpenRouter slug, smoke-tested live with `response_format: json_object` — never a paid model,
so a deploy that forgets a variable cannot start spending money. The three slots sit on three
upstream providers (Google AI Studio, Phala, AtlasCloud), so one provider's outage cannot empty the
cascade:

| Slot | Env var | Default |
|---|---|---|
| primary | `OPENROUTER_MODEL_PRIMARY` | `google/gemma-4-26b-a4b-it:free` |
| fast | `OPENROUTER_MODEL_FAST` | `xingchen-agi/xing4.0-29b-a4b:free` (3.6 s, valid JSON) |
| fallback | `OPENROUTER_MODEL_FALLBACK` | `dots-studio/dots-3-note-preview:free` (a reasoning model: slower, the best verdict of the three tried) |

History: `minimax/minimax-m3.1-flash:free` (fast and fallback since 2026-10-21) left OpenRouter on
2026-10-26, and that morning's debate failed on its 404s; `google/gemma-4-31b-it:free` answered 502
on 2026-10-21 and again on 2026-10-26, so it is not used.

The previous defaults (`openai/gpt-4o-mini`, `anthropic/claude-3-haiku`) were paid models, and the
slugs the README named (`google/gemini-2.0-flash-001`, `google/gemini-2.5-flash-lite`) are no longer
what the deploy runs.

Cases (`tests/unit/test_config.py`):

- **D1** with no env and no `.env`, the three slots default to the slugs above.
- **D2** every default ends in `:free` (negative space: no paid default can sneak back in).
- **D3** each `OPENROUTER_MODEL_*` variable still overrides its slot.
- **D4** `_resolve_model` maps `primary`/`fast`/`fallback` to the configured slot (existing router tests).

### JSON replies from the free models (2026-10-20)

The first live debate on the free models failed in its plan node: `google/gemma-4-26b-a4b-it:free`
honoured JSON mode but answered `[{"sub_questions": [...], "search_queries": [...]}]`, the object
wrapped in a one-element array, and `PlanSchema.model_validate(list)` raised `schema_mismatch`. The
whole debate went to `error` on one call, and the fallback was never asked, because only transport
and HTTP failures fell through to it.

`chat_json` now parses a reply in one pure step (`parse_json_reply`) and treats an unusable reply
like a failed tier:

- a reply fenced as ` ```json … ``` ` (or bare ` ``` `) is unwrapped before parsing;
- a top-level array holding exactly one JSON object is that object;
- anything else is parsed as is: invalid JSON → `LLMError("invalid_json")`, a shape the schema
  rejects (including an empty array, an array of two objects, an array of non-objects) →
  `LLMError("schema_mismatch")`; it never guesses which of several objects was meant;
- when a tier's reply is unusable (or the call fails), the next tier of the cascade is asked (see
  "The cascade" below); its reply goes through the same parse.

Cases (`tests/unit/test_llm_router.py`):

- **J1** a one-element array holding the object validates as that object (the live failure).
- **J2** a ` ```json ` fenced object validates; so does a bare ` ``` ` fence.
- **J3** an unusable primary reply (invalid JSON, schema mismatch, a two-object array) is not
  retried on the same model: the next tier (fast) is asked once and its valid reply returned.
- **J4** every tier's reply unusable → `LLMError("all_models_exhausted")` naming each tier's stage
  (`invalid_json` or `schema_mismatch`).
- **J5** `[]`, `[1]` and `[{…}, {…}]` are `schema_mismatch`, never unwrapped.

### The cascade (2026-10-21)

Two debates bastion started failed on transient upstream errors instead of cascading:
`server_error: status=504` (a 5xx was never retried, and `chat_json` raised the fallback's error),
then `all_models_exhausted: primary=status=502; fallback=status=504`, which went primary →
fallback and never asked the fast tier. The convention (`convention-llm-usage`) is PRIMARY → FAST →
FALLBACK on a 429 or a 5xx.

`chat` and `chat_json` now share one cascade:

1. **Order.** The requested tier first, then the others in PRIMARY → FAST → FALLBACK order
   (`primary` → primary, fast, fallback; `fast` → fast, primary, fallback). A model slug already
   tried is skipped, so two tiers naming the same model never call it twice.
2. **Transient failures** — 429, any 5xx (500, 502, 503, 504, …), a request timeout and a dropped
   connection — are retried on the same tier with the existing policy (5 attempts, exponential
   backoff `1.5 s × 2^n` plus up to 0.75 s jitter), then the cascade moves to the next tier.
3. **Other failures** — a 4xx other than 429, a 200 without a completion, a JSON reply the schema
   cannot read — move to the next tier at once (retrying the same model would get the same answer).
4. **Exhaustion.** Only when every tier has failed: `LLMError("all_models_exhausted",
   "primary=<stage>; fast=<stage>; …")`.
5. **Time bound.** One `chat`/`chat_json` call — one node's LLM work, since each node makes one —
   takes at most `LLM_NODE_DEADLINE_S` (default **120 s**). Each tier gets an equal share of what is
   left when it starts (40 s for the first of three), every attempt is cut at the end of its tier's
   share, and a backoff that would end past it is not slept: the tier is given up as `timeout` and
   the next one starts. A hung model therefore cannot keep the later tiers from being asked, and a
   node cannot hang past the bound; the whole debate stays under `debate_deadline_s` (300 s).

Cases (`tests/unit/test_llm_router.py`):

- **C1** 504 on every primary attempt → the fast tier answers; calls: primary × 5, then fast × 1.
- **C2** every tier answering 500, 502, 503 or 504 → `all_models_exhausted` naming every tier, each
  tried 5 times.
- **C3** a request timeout counts as a 5xx: retried on its tier, then the fast tier answers.
- **C4** a 504 then a 200 on the primary → answered by the primary after one backoff (retry before
  moving on).
- **C5** a 400 on the primary → no retry, the fast tier answers (1 primary call).
- **C6** `chat_json`: 504s on the primary → the fast tier's valid JSON is returned.
- **C7** `model="fast"` → fast, then primary, then fallback.
- **C8** fast and fallback configured to the same slug → that model is called once per attempt
  budget, never as a second tier; the error names only the tiers tried.
- **C9** every tier hanging → `all_models_exhausted` within the deadline (a 0.3 s deadline answers
  in well under a second), each tier having been tried.
- **C10** a backoff that would outlast the tier's share is not slept.
- **C11** a tier that starts with no time left is given up as `timeout` without a call; the bound
  must be positive (`LLM_NODE_DEADLINE_S` ≤ 0 is rejected at startup).

## Invariants

- All calls go through the OpenRouter `/chat/completions` endpoint at `OPENROUTER_BASE_URL`.
- Every outgoing request has the `HTTP-Referer` and `X-Title` headers set from env.
- `chat_json` uses OpenAI JSON mode (`response_format={"type": "json_object"}`) and validates the string response against the supplied Pydantic schema before returning.
- On 429, 5xx, a timeout or a dropped connection: retry the tier, then cascade ("The cascade" above); `all_models_exhausted` only after every tier fails.
- Hard per-attempt timeout = 60 s (`OPENROUTER_TIMEOUT_S`), cut shorter by the tier's share of the 120 s node bound.

## Test cases (respx)

1. Happy path primary → returns assistant content string.
2. `chat_json` returns a validated Pydantic instance.
3. Primary 429 → fallback called → returns fallback content.
4. Primary 500 → fallback called.
5. Primary 200 but malformed JSON (to chat_json) → one retry on same model with `temperature=0` → then raises `LLMError`.
6. Fallback also 429 → raises `LLMError("all_models_exhausted")`.
7. Timeout exceeded → raises `LLMError("timeout")`.
8. Missing `OPENROUTER_API_KEY` at startup → crashes with a helpful message.
9. Outgoing request carries `HTTP-Referer: https://github.com/Abdul-Muizz1310` and `X-Title: muizz-lab-portfolio`.
10. `schema` validation failure on `chat_json` raises `LLMError("schema_mismatch")` with the offending JSON attached.

## Acceptance

- `LLMError` is a typed exception with `stage` (`"primary"|"fallback"|"schema"|"timeout"`) and `detail`.
- No secrets logged (request bodies masked).
- Coverage ≥90% on `core/llm.py`.
