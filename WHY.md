# Why paper-trail?

## The obvious version

Everyone builds a "ChatGPT with citations" demo. One LLM call, one confident answer, a few footnotes pasted in after the fact. The footnotes rarely support the claim and nobody checks. Worse, a single model in a single call has one shot to get it right — if the model happens to believe a popular myth (Great Wall visible from space, goldfish 3-second memory, 10% of brain, red enrages bulls), you get that myth back with confidence.

## Why I built it differently

paper-trail runs a *debate*, not a lookup. A Proponent and a Skeptic each argue the claim, pulling live web evidence in parallel. A Judge reads both sides against an explicit rubric and either converges on a verdict or sends the state back through another round. The transcript is the product — you read *why* the verdict landed where it did, not just a rating.

Three concrete things matter here:

**It's a real cyclic state machine.** The `judge → proponent/skeptic` back-edge is an actual `add_conditional_edges` call in LangGraph, gated on `confidence < 0.85 && round < max_rounds`. "Another round" is a real edge, not a retry loop hidden inside one prompt's for-loop. You can watch it run as Server-Sent Events, and every hop through every node shows up as a nested span in LangFuse with its input, output, model, token usage, and any tool calls beneath it.

**The prompts have mental models, not just instructions.** The Judge is explicitly told it's "a neutral adjudicator weighing evidence, not scoring rhetoric", that common misconceptions are **FALSE** with high confidence (not INCONCLUSIVE — the easy escape hatch), and that "lots of people believe it" is never evidence. The Proponent is "an honest lawyer, not a PR agent" and is explicitly permitted to concede if the evidence actually contradicts the claim. This one change took the eval set from 60% to 100% on the first smoke run: two claims that were failing (Great Wall visible from space, 10% brain) went from `TRUE conf=1.0` to `FALSE conf=0.95-1.00`.

**The gate to ship was concrete.** Twenty-five labelled claims — TRUE, FALSE, and INCONCLUSIVE — form the eval set. Real-mode eval runner hits the live LangGraph against real OpenRouter calls and exits non-zero if accuracy < 80%. The first full run on `main` scored **21/25 = 84%**, with 20/20 correct on clear TRUE/FALSE claims and the misses concentrated on five genuinely contested health claims (red wine longevity, intermittent fasting, Mozart effect, multivitamin mortality, artificial sweeteners) where the Judge preferred `FALSE` over `INCONCLUSIVE`. Arguably the Judge was right and the ground-truth labels are too lenient — but we measure against labels, so they count as misses.

## What I'd change if I did it again

The v0.1 shipped as a single-model deployment with free-tier fallbacks, and rate limits were the biggest actual engineering problem of the week. A cleaner v0.2 would:

- **Add retries at the graph level, not just the LLM level.** Right now `core/llm.py` does jittered exponential backoff on 429 at a single tier, and the router cascades primary → fallback. But when both are saturated, the whole graph fails instead of pausing. A smarter runner would detect shared-pool exhaustion and insert a cooldown between rounds.
- **Cache Tavily results aggressively.** Right now every debate re-queries even if a previous debate asked the same question five minutes ago. Upstash is provisioned; the key is `hash(query) + 24h`.
- **Let the Judge request a specific missing source** instead of just asking for "more rounds". Right now the feedback to Proponent/Skeptic is only "your confidence isn't high enough, argue again" — but the Judge often knows exactly *what* is missing ("no primary source on post-2020 cardiovascular outcomes") and could pass that forward as a plan delta.
- **Swap the v0.1 scope of the memory tool back in.** The pgvector extension is live on Neon, the `Debate` table is there, but the trim to hit S6 dropped the memory tool and the inkprint-style embedding of prior debates. A v0.2 with cross-debate memory would let paper-trail build up a local corpus of verified claims over time instead of re-searching the web every run.

The biggest lesson from the week was that **observability is the most important thing to build second**. The first LangFuse wrapper I wrote targeted the v2 SDK API while the installed package was v4 — every call raised `AttributeError`, got swallowed by the error-tolerance layer, and produced empty trace shells for ~12 hours before I noticed. Rewriting it against v3's OTel-backed span API and instrumenting every node + tool + LLM call took half a day and paid for itself twice over during the prompt rewrite: every failed claim was inspectable end-to-end, so "why did it say FALSE" took 30 seconds to answer instead of 30 minutes. Build it early.
