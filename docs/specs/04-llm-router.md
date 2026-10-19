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

### Default slugs (2026-10-20)

The env vars win; the code defaults apply only when a slot's variable is unset. Every default is a
`:free` OpenRouter slug (verified live 2026-10-20) — never a paid model, so a deploy that forgets a
variable cannot start spending money:

| Slot | Env var | Default |
|---|---|---|
| primary | `OPENROUTER_MODEL_PRIMARY` | `google/gemma-4-26b-a4b-it:free` |
| fast | `OPENROUTER_MODEL_FAST` | `minimax/minimax-m3.1-flash:free` |
| fallback | `OPENROUTER_MODEL_FALLBACK` | `google/gemma-4-31b-it:free` |

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
- when the primary tier's reply is unusable (or the call fails), the fallback tier is asked once;
  its reply goes through the same parse, and its failure is the one raised.

Cases (`tests/unit/test_llm_router.py`):

- **J1** a one-element array holding the object validates as that object (the live failure).
- **J2** a ` ```json ` fenced object validates; so does a bare ` ``` ` fence.
- **J3** an unusable primary reply (invalid JSON, schema mismatch, a two-object array) asks the
  fallback once and returns the fallback's valid reply.
- **J4** both replies unusable → `LLMError` with the fallback's stage (`invalid_json` or
  `schema_mismatch`).
- **J5** `[]`, `[1]` and `[{…}, {…}]` are `schema_mismatch`, never unwrapped.

## Invariants

- All calls go through the OpenRouter `/chat/completions` endpoint at `OPENROUTER_BASE_URL`.
- Every outgoing request has the `HTTP-Referer` and `X-Title` headers set from env.
- `chat_json` uses OpenAI JSON mode (`response_format={"type": "json_object"}`) and validates the string response against the supplied Pydantic schema before returning.
- On 429 or 5xx from primary → retry once on fallback; if fallback also fails → raise `LLMError`.
- Hard per-call timeout = 60s.

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
