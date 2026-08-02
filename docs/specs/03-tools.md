# Spec 03 — Tools

> **Scope note (kept honest).** Two parts of this spec were **descoped before
> v0.1 shipped and are NOT implemented**: the Upstash search cache (cases 5–6)
> and the vector memory tool (`agents/tools/memory.py`, cases 11–14, and the
> pgvector acceptance line). Neither module exists, no embeddings are computed
> anywhere in `src/`, and `voyageai`/`pgvector` are no longer declared as
> dependencies. They are left written down as the design record for a v0.2 —
> see "cache Tavily results" and "swap the memory tool back in" in `WHY.md`.
> What ships: `search`, `fetch`, `cite`, plus `transcript.py` (canonicalization
> + SHA-256 hashing), which post-dates this spec.

## Goal

Tools callable by the graph: web search (Tavily), article fetch (trafilatura),
and citation formatting. *(Planned, not shipped: vector memory via pgvector.)*

## Modules

- `agents/tools/search.py` — `async def search(query: str, k: int = 5) -> list[SearchHit]`
- `agents/tools/fetch.py` — `async def fetch(url: str) -> FetchedDoc`
- `agents/tools/cite.py` — `def format_citation(hit: SearchHit) -> str`
- `agents/tools/memory.py` — **NOT IMPLEMENTED** (descoped): `async def remember(text: str, debate_id: UUID)`, `async def recall(query: str, k: int) -> list[MemoryHit]`

## Test cases

**search (respx-mocked):**
1. Happy path: mocked Tavily response → returns `k` typed `SearchHit` objects.
2. Empty query → `ValueError`.
3. Tavily 429 → raises `ToolError`. *(As shipped: `search()` raises `ToolError("tavily_error")` on any non-200 and `ToolError("tavily_http_error")` on a transport failure; retry/backoff lives one layer up in `core/llm.py`, not in the tool.)*
4. Tavily 500 → same as 429.
5. ~~Identical query called twice → second call is served from Upstash cache.~~ **DESCOPED — no cache exists.**
6. ~~Cached response older than 24h is ignored.~~ **DESCOPED.**

**fetch (HTML fixture `tests/fixtures/sample_article.html`):**
7. A committed Wikipedia article HTML → trafilatura extracts ≥500 chars of main text.
8. 404 response → raises `ToolError("fetch_not_found")`.
9. Non-HTML content-type → `ToolError("fetch_not_html")`.
10. Extracted text stripped of trailing whitespace and normalized to NFC.

**memory (Testcontainers Postgres + pgvector) — DESCOPED, none of these exist:**
11. ~~`remember` writes a row with a non-null embedding.~~
12. ~~`recall("same as first insert")` returns the inserted row as top-1.~~
13. ~~`recall` on an empty table returns `[]`.~~
14. ~~Embedding dimensionality matches `voyage-3-lite` (1024).~~

**cite:**
15. Given a `SearchHit` with title/url/published_date → returns `"[Title](url) — YYYY-MM-DD"`.
16. Missing published_date → omits the date gracefully.
17. Title with markdown special chars is escaped.

## Acceptance

- Every tool is fully typed and async (except `cite`, which is pure sync).
- `ToolError` is a dedicated exception.
- ~~Pgvector test uses a real Postgres with the `vector` extension installed via Testcontainers.~~
  **DESCOPED with the memory tool.** The Testcontainers Postgres tier does exist
  (`tests/integration/`) but covers cross-session write visibility, not vectors.
