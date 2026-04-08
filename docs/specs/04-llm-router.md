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
