"""Render node — deterministic markdown transcript + structured rounds + hash."""

from __future__ import annotations

from typing import Any

from paper_trail.agents.state import DebateState
from paper_trail.agents.tools.transcript import hash_transcript
from paper_trail.core.langfuse import span, update_current_span


def _build_rounds_struct(
    rounds: list[Any],
) -> list[dict[str, Any]]:
    """Convert the state rounds into the structured transcript shape.

    Output order is stable: sort by round ascending, then by side
    (proponent < skeptic) so the rendered transcript is deterministic
    regardless of the order LangGraph happened to emit the parallel
    branches.
    """
    # Upstream guarantees every entry is a dict (proponent/skeptic nodes
    # return typed RoundEntry payloads); the markdown builder depends on
    # that too. No defensive isinstance check here.
    out: list[dict[str, Any]] = []
    for entry in sorted(
        rounds,
        key=lambda e: (int(e.get("round", 0) or 0), str(e.get("side", ""))),
    ):
        citations_raw = entry.get("citations") or []
        citations: list[dict[str, Any]] = []
        for c in citations_raw:
            if not isinstance(c, dict):
                continue
            ctype = c.get("type")
            ref = c.get("ref")
            if ctype not in ("cert", "url") or not ref:
                continue
            citations.append(
                {
                    "type": ctype,
                    "ref": str(ref),
                    "title": str(c.get("title") or ""),
                }
            )
        out.append(
            {
                "side": str(entry.get("side", "")),
                "round": int(entry.get("round", 0)),
                "argument_md": str(entry.get("argument", "")),
                "citations": citations,
            }
        )
    return out


async def render(state: DebateState) -> dict[str, Any]:
    """Build a deterministic markdown transcript, structured rounds, and hash."""
    claim = state.get("claim", "")
    verdict = state.get("verdict")
    confidence = state.get("confidence")
    reasoning = (state.get("reasoning") or "").strip()
    rounds = state.get("rounds") or []

    async with span(
        "node.render",
        input={"claim": claim, "rounds_count": len(rounds), "verdict": verdict},
    ):
        lines: list[str] = []
        lines.append(f"# Debate: {claim}")
        lines.append("")
        if not rounds:
            lines.append("_No rounds run._")
        else:
            by_round: dict[int, list[dict[str, Any]]] = {}
            for r in rounds:
                by_round.setdefault(int(r["round"]), []).append(dict(r))
            for rnum in sorted(by_round):
                lines.append(f"## Round {rnum}")
                entries = sorted(by_round[rnum], key=lambda e: str(e["side"]))
                for e in entries:
                    lines.append(f"### {str(e['side']).title()}")
                    lines.append(str(e.get("argument", "")))
                    evidence = e.get("evidence") or []
                    if evidence:
                        lines.append("")
                        lines.append("**Evidence:**")
                        for ev in evidence:
                            if isinstance(ev, dict):
                                url = ev.get("url", "")
                                title = ev.get("title", url)
                                lines.append(f"- [{title}]({url})")
                    lines.append("")
        lines.append("## Verdict")
        lines.append(f"- Verdict: **{verdict}**")
        lines.append(f"- Confidence: {confidence}")
        if reasoning:
            lines.append("")
            lines.append("## Reasoning")
            lines.append(reasoning)
        transcript = "\n".join(lines)

        # Structured rounds + deterministic hash. The hash covers the
        # logical payload, not the markdown, so it's stable across render
        # changes that don't affect the debate substance.
        rounds_struct = _build_rounds_struct(list(rounds))
        hash_verdict = str(verdict) if verdict is not None else "INCONCLUSIVE"
        hash_confidence = float(confidence) if confidence is not None else 0.0
        transcript_hash = hash_transcript(
            claim=claim,
            verdict=hash_verdict,
            confidence=hash_confidence,
            rounds=rounds_struct,
        )
        update_current_span(
            output={
                "transcript_length": len(transcript),
                "verdict": verdict,
                "confidence": confidence,
                "rounds_struct_count": len(rounds_struct),
                "transcript_hash": transcript_hash,
            }
        )
        return {
            "transcript_md": transcript,
            "rounds_struct": rounds_struct,
            "transcript_hash": transcript_hash,
        }
