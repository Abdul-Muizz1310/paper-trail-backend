# paper-trail architecture

## System overview

```mermaid
flowchart LR
    Client[Client<br/>Next.js frontend] -->|POST /debates| API[FastAPI router]
    API -->|create row| DB[(Neon Postgres<br/>+ pgvector)]
    API -->|enqueue| Graph[LangGraph<br/>debate state machine]

    Graph --> Plan[Plan node]
    Plan --> Prop[Proponent node]
    Plan --> Skep[Skeptic node]
    Prop -->|Tavily + Trafilatura| Web[(Web evidence)]
    Skep -->|Tavily + Trafilatura| Web
    Prop --> Judge[Judge node]
    Skep --> Judge
    Judge -->|converged| Render[Render node]
    Judge -.->|need_more| Prop
    Judge -.->|need_more| Skep
    Render --> DB

    API -->|SSE /debates/:id/stream| Client

    Graph -.->|traces| LF[LangFuse]
    Graph -.->|LLM calls| OR[OpenRouter]
    Prop -.->|embeddings| Voy[Voyage AI]
```

## Component map

| Layer | Key files | Responsibility | Never touches |
|---|---|---|---|
| `main.py` | App factory, lifespan, middleware registration | -- | Business logic |
| `api/routers/` | `debates.py`, `health.py` | HTTP shape, request validation, SSE streaming | DB, LLMs |
| `services/` | `debate_service.py` | Orchestration -- create debate, launch graph, stream events | HTTP, raw SQL |
| `repositories/` | `debate_repo.py` | Async SQLAlchemy queries on Debate rows + embeddings | HTTP, LLMs |
| `models/` | `debate.py` | SQLAlchemy declarative models (Debate, Round, Evidence) | Anything non-DB |
| `schemas/` | `debate.py`, `events.py` | Pydantic DTOs at the HTTP + SSE boundary | DB |
| `agents/graph.py` | `build_graph()` | LangGraph `StateGraph` assembly, edge definitions, compile | HTTP |
| `agents/state.py` | `DebateState` | TypedDict state schema shared by all nodes | HTTP |
| `agents/nodes/` | `plan.py`, `proponent.py`, `skeptic.py`, `judge.py`, `render.py`, `_format.py` | Individual graph nodes -- each is a pure function `(state) -> state` | HTTP, DB writes |
| `agents/tools/` | `search.py`, `fetch.py`, `cite.py` | LangChain tools bound to proponent/skeptic nodes | DB, HTTP |
| `agents/prompts/` | Per-node prompt templates | System/user prompts with Jinja-style variable slots | Everything |
| `core/config.py` | `Settings` | pydantic-settings env loading, secret resolution | Everything above |
| `core/db.py` | Engine, session factory | Async SQLAlchemy engine + `get_session` dependency | Everything above |
| `core/llm.py` | `get_chat_model()` | OpenRouter LLM client with primary/fallback cascade | Everything above |
| `core/langfuse.py` | `get_tracer()` | LangFuse tracing wrapper, fail-safe decorator | Everything above |
| `core/platform_auth.py` | Middleware | Render platform auth, CORS, request-id propagation | Everything above |
| `platform/` | Health, readiness | Platform-level probes for Render | Everything above |

## MVC layering

```mermaid
flowchart TD
    subgraph HTTP ["HTTP layer"]
        Routers["api/routers/<br/>debates.py, health.py"]
    end

    subgraph Business ["Business layer"]
        Services["services/<br/>debate_service.py"]
        Agents["agents/<br/>graph + nodes + tools"]
    end

    subgraph Data ["Data layer"]
        Repos["repositories/<br/>debate_repo.py"]
        Models["models/<br/>debate.py"]
    end

    subgraph Shared ["Shared"]
        Schemas["schemas/<br/>DTOs + SSE events"]
        Core["core/<br/>config, db, llm, langfuse"]
    end

    Routers --> Services
    Routers -.-> Schemas
    Services --> Repos
    Services --> Agents
    Agents -.-> Core
    Repos --> Models
    Repos --> Core
```

## Debate lifecycle

```mermaid
sequenceDiagram
    participant C as Client
    participant R as Router
    participant S as DebateService
    participant G as LangGraph
    participant P as Plan
    participant Pro as Proponent
    participant Skep as Skeptic
    participant J as Judge
    participant Ren as Render
    participant DB as Neon Postgres

    C->>R: POST /debates {claim}
    R->>S: create_debate(claim)
    S->>DB: INSERT debate row
    S->>G: spawn graph(debate_id, claim)
    R-->>C: 201 {debate_id}

    C->>R: GET /debates/:id/stream (SSE)

    G->>P: plan(state)
    P-->>C: SSE: plan_complete
    par Parallel research
        G->>Pro: proponent(state)
        Pro-->>C: SSE: proponent_argument
    and
        G->>Skep: skeptic(state)
        Skep-->>C: SSE: skeptic_argument
    end
    G->>J: judge(state)
    alt need_more rounds
        J-->>C: SSE: judge_feedback
        J->>Pro: loop back
        J->>Skep: loop back
    else converged
        J-->>C: SSE: judge_verdict
        G->>Ren: render(state)
        Ren->>DB: UPDATE debate (verdict, transcript)
        Ren-->>C: SSE: debate_complete
    end
```

## Concurrency model

- **Parallel debate agents**: Proponent and Skeptic run as parallel LangGraph edges from `plan`; both feed `judge` via a fan-in barrier. This halves wall-clock time per round.
- **LLM cascade**: `core/llm.py` tries `OPENROUTER_MODEL_PRIMARY`, falls back to `_FALLBACK` on 429/5xx. JSON mode enforced for the Judge node to guarantee parseable verdicts.
- **SSE streaming**: The router opens an `EventSource`-compatible stream. Each graph node emits typed events (`plan_complete`, `proponent_argument`, `skeptic_argument`, `judge_verdict`, `debate_complete`) that the service relays as SSE frames.
- **Evidence caching**: Tavily search responses cached in Upstash keyed by `hash(query)` with 24h TTL -- repeat debates on similar claims avoid redundant web fetches.
- **Fail-safe tracing**: LangFuse wraps every node; failures in tracing are caught and logged, never failing the request.

## Observability hierarchy

```mermaid
flowchart TD
    Trace["LangFuse Trace<br/>(1 per debate)"] --> PlanSpan["plan span"]
    Trace --> ProSpan["proponent span"]
    Trace --> SkepSpan["skeptic span"]
    Trace --> JudgeSpan["judge span"]
    Trace --> RenderSpan["render span"]

    ProSpan --> ToolSpan1["search tool span"]
    ProSpan --> ToolSpan2["fetch tool span"]
    ProSpan --> LLMSpan1["LLM generation span"]

    SkepSpan --> ToolSpan3["search tool span"]
    SkepSpan --> LLMSpan2["LLM generation span"]

    JudgeSpan --> LLMSpan3["LLM generation span<br/>(JSON mode)"]
```

Each trace captures: model used, token counts, latency, tool inputs/outputs, and the full state diff produced by every node. Traces are queryable in the LangFuse dashboard for debugging slow rounds or hallucinated evidence.
