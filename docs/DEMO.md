# paper-trail demo script

> Everything here runs against the live backend at `https://paper-trail-backend-7h27.onrender.com`. No frontend required — `paper-trail-frontend` is Phase 2b. Swap in `http://localhost:8000` if you're running locally.

## 60-second demo (synchronous)

The platform endpoint runs the graph end-to-end in one HTTP call and caps at 3 rounds, which is perfect for a scripted demo.

```bash
BASE=https://paper-trail-backend-7h27.onrender.com

curl -sS -X POST "$BASE/platform/debate" \
  -H 'Authorization: Bearer demo' \
  -H 'Content-Type: application/json' \
  -d '{"claim":"Humans only use 10 percent of their brains.","max_rounds":2}'
```

Expected response (typically 30–60s wall time on Render free tier):

```json
{
  "debate_id": "4f07347a-001b-4925-9987-519a4b8570d0",
  "transcript_url": "/debates/4f07347a-001b-4925-9987-519a4b8570d0/transcript.md",
  "verdict": "FALSE",
  "confidence": 0.95,
  "rounds_run": 2
}
```

Fetch the full markdown transcript with inline citations:

```bash
curl -sS "$BASE/debates/4f07347a-001b-4925-9987-519a4b8570d0/transcript.md"
```

You should see:

```markdown
# Debate: Humans only use 10 percent of their brains.

## Round 1
### Proponent
The evidence does not support this claim...

**Evidence:**
- [The ten-percent myth — Scientific American](https://...)
- [Neuroscience: The myth of the 10% brain](https://...)

### Skeptic
This is a well-documented popular misconception...

**Evidence:**
- [Snopes — 10% of brain myth](https://...)
...

## Verdict
- Verdict: **FALSE**
- Confidence: 0.95
```

## 90-second demo (streaming)

This matches how the web UI will consume the backend in Phase 2b. The `/debates` endpoint returns immediately with a `stream_url`; the graph runs in a background task and emits Server-Sent Events as state changes.

```bash
BASE=https://paper-trail-backend-7h27.onrender.com

# 1. create — returns immediately
DEBATE=$(curl -sS -X POST "$BASE/debates" \
  -H 'Content-Type: application/json' \
  -d '{"claim":"The Great Wall of China is visible from low Earth orbit with the naked eye.","max_rounds":3}')
ID=$(echo "$DEBATE" | python -c "import sys,json;print(json.load(sys.stdin)['debate_id'])")
echo "debate_id: $ID"

# 2. subscribe — watch state transitions
curl -N "$BASE/debates/$ID/stream"
```

Expected event stream:

```
event: state
data: {"type":"state","status":"running","verdict":null,"confidence":null,"rounds_count":0}

event: state
data: {"type":"state","status":"running","verdict":"FALSE","confidence":0.92,"rounds_count":2}

event: done
data: {"type":"done","status":"done","verdict":"FALSE","confidence":0.95,"rounds_count":2}
```

Fetch the transcript the same way as above via `$BASE/debates/$ID/transcript.md`.

## What to point to in an interview / demo

- **LangGraph cycle, not a loop.** The `judge → proponent/skeptic` back-edge is a real `add_conditional_edges` call in [`src/paper_trail/agents/graph.py`](../src/paper_trail/agents/graph.py). Walk the reviewer through `is_converged` and the `need_more` router.
- **Parallel evidence construction.** Proponent and Skeptic run concurrently from the `plan` node — the LangFuse trace shows two overlapping `llm.chat` generations under the same round.
- **Evals, not vibes.** [`evals/report.md`](../evals/report.md) has the real 25-claim accuracy (21/25 = 84%) with per-claim wall time and rounds. Run `uv run python -m evals.run_eval` to reproduce.
- **Honest, rubric-driven prompts.** Open [`src/paper_trail/agents/prompts/judge.md`](../src/paper_trail/agents/prompts/judge.md). Note the explicit rule that common misconceptions are `FALSE` (not `INCONCLUSIVE`) and the 0.55–1.00 banded confidence rubric.
- **Observability-first.** Every debate produces a LangFuse trace with `debate.run` at the root, nested `node.*` spans beneath it, `llm.json`/`llm.chat` generations with full messages + token usage, and `tool.search` spans per Tavily call. Trace-level tags include `verdict:*`, `model_primary:*`, `status:done|error`, and the `session_id` is the debate UUID so you can filter by session.
- **Negative-space correctness.** Malformed bodies → `422` from Pydantic v2 DTOs. Missing bearer token on `/platform/debate` → `401`. `max_rounds > 10` → `422`. `DATABASE_URL` missing at alembic boot → clear error, not silent fallthrough. See the [red-test commits](https://github.com/Abdul-Muizz1310/paper-trail-backend/commits/main?after=2024) for every failure case enumerated before implementation.

## A few claims that demo well

| Claim | Expected verdict | Notes |
|---|---|---|
| `Water boils at 100 degrees Celsius at sea level.` | TRUE (≥0.9) | Clean round-1 convergence |
| `The Great Wall of China is visible from low Earth orbit with the naked eye.` | FALSE (≥0.9) | Classic misconception — tests the Judge's misconception rubric |
| `Humans only use 10 percent of their brains.` | FALSE (≥0.9) | Same category |
| `Regular multivitamin use reduces all-cause mortality in healthy adults.` | FALSE or INCONCLUSIVE | Genuinely contested — the Judge tends to pick FALSE based on Cochrane evidence, which is defensible |
| `Einstein failed mathematics in school.` | FALSE | Named in the judge rubric as a known myth |

## Troubleshooting the demo

- **First call is slow (~20s extra):** Render free tier cold-starts the container. Second call onward is fast.
- **`HTTP 500` on `GET /debates`:** transient DB connection hiccup on cold start; retry once.
- **`verdict: INCONCLUSIVE` when you expected a clear answer:** the primary model may have 429'd and fallen through to the fallback on every LLM call. Check the trace in LangFuse — if most generations are on the fallback model, upgrade OpenRouter BYOK or wait for rate limits to reset.
- **Nothing in LangFuse:** confirm `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL` are set in the Render dashboard. The wrapper degrades to a silent no-op on missing config (by design — tracing must never fail a request).
