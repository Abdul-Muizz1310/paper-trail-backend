"""Render node — deterministic markdown transcript."""

from __future__ import annotations

from typing import Any

from paper_trail.agents.state import DebateState


async def render(state: DebateState) -> dict[str, Any]:
    """Build a deterministic markdown transcript from state."""
    lines: list[str] = []
    claim = state.get("claim", "")
    lines.append(f"# Debate: {claim}")
    lines.append("")
    rounds = state.get("rounds") or []
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
    verdict = state.get("verdict")
    confidence = state.get("confidence")
    lines.append(f"- Verdict: **{verdict}**")
    lines.append(f"- Confidence: {confidence}")
    return {"transcript_md": "\n".join(lines)}
