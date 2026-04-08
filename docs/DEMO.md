# paper-trail demo script

> Written at S6. Placeholder until the eval run closes Phase 2's acceptance gate.

## 60-second demo flow

1. Open `https://paper-trail-frontend.vercel.app/`.
2. Paste the canned claim: *"Regular multivitamin use reduces all-cause mortality in healthy adults."*
3. Hit **Debate**. Watch the two-panel arena stream:
   - Left (Proponent) pulls three meta-analyses.
   - Right (Skeptic) pulls the 2022 Cochrane review.
4. Judge scores round 1 at 0.62 → sends back for round 2.
5. Round 2 converges at 0.88 → verdict **FALSE** with a one-paragraph rationale.
6. Click **Transcript** → markdown view with inline citations.

## What to point to in an interview

- **LangGraph cycle, not a loop.** The Judge → Proponent/Skeptic back-edge is a real `add_conditional_edges` call, visible in `src/paper_trail/agents/graph.py`.
- **Parallel evidence fetching.** Proponent and Skeptic run concurrently — trace in LangFuse shows overlapping spans.
- **Evals, not vibes.** `evals/report.md` has the 25-claim accuracy number from the latest run.
- **Negative space.** Tampered SSE events are rejected by the client, malformed claim bodies by the router, missing env vars crash boot.
