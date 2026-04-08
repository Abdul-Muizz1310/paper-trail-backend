# Spec 03 — Tools

## Goal

Four tools callable by nodes: web search (Tavily), article fetch (trafilatura), vector memory (pgvector), and citation formatting.

## Modules

- `agents/tools/search.py` — `async def search(query: str, k: int = 5) -> list[SearchHit]`
- `agents/tools/fetch.py` — `async def fetch(url: str) -> FetchedDoc`
- `agents/tools/memory.py` — `async def remember(text: str, debate_id: UUID)`, `async def recall(query: str, k: int) -> list[MemoryHit]`
- `agents/tools/cite.py` — `def format_citation(hit: SearchHit) -> str`

## Test cases

**search (respx-mocked + Upstash cache):**
1. Happy path: mocked Tavily response → returns `k` typed `SearchHit` objects.
2. Empty query → `ValueError`.
3. Tavily 429 → raises `ToolError("tavily_rate_limited")` after exhausting retries (tenacity, 2 attempts).
4. Tavily 500 → same as 429.
5. Identical query called twice → second call is served from Upstash cache (Tavily called exactly once).
6. Cached response older than 24h is ignored.

**fetch (HTML fixture under `tests/fixtures/html/`):**
7. A committed Wikipedia article HTML → trafilatura extracts ≥500 chars of main text.
8. 404 response → raises `ToolError("fetch_not_found")`.
9. Non-HTML content-type → `ToolError("fetch_not_html")`.
10. Extracted text stripped of trailing whitespace and normalized to NFC.

**memory (Testcontainers Postgres + pgvector):**
11. `remember` writes a row with a non-null embedding.
12. `recall("same as first insert")` returns the inserted row as top-1.
13. `recall` on an empty table returns `[]`.
14. Embedding dimensionality matches `voyage-3-lite` (1024).

**cite:**
15. Given a `SearchHit` with title/url/published_date → returns `"[Title](url) — YYYY-MM-DD"`.
16. Missing published_date → omits the date gracefully.
17. Title with markdown special chars is escaped.

## Acceptance

- Every tool is fully typed and async (except `cite`, which is pure sync).
- `ToolError` is a dedicated exception.
- Pgvector test uses a real Postgres with the `vector` extension installed via Testcontainers.
