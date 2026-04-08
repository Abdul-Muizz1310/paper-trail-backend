# Why paper-trail?

## The obvious version

Everyone builds a "ChatGPT with citations" demo. One LLM call, one confident answer, a few footnotes pasted in after the fact. The footnotes rarely support the claim and nobody checks.

## Why I built it differently

paper-trail runs a *debate*, not a lookup. A Proponent and a Skeptic each argue the claim, pulling web evidence in parallel. A Judge reads both sides, scores confidence, and either converges or sends the round back for another pass. The transcript is the product — you see *why* the verdict landed where it did, not just a rating. The whole thing is a LangGraph cyclic state machine so "another round" is a real edge, not a retry loop hidden inside a prompt.

Twenty-five labeled claims form the eval set. The gate to ship was ≥80% accuracy with ≤3 rounds average and p95 under 30 seconds — all measured, not vibes.

## What I'd change if I did it again

The first cut had the Judge calling tools directly; moving all evidence fetching into Proponent/Skeptic made traces cleaner and cut latency. Next iteration: swap SimHash caching in front of Tavily for more aggressive reuse on repeated sub-questions, and let the Judge request a specific source it feels is missing instead of just asking for "more rounds".
