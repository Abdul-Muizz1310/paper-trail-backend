"""Shared formatters for node user-message construction.

Small models parse structured markdown more reliably than Python-repr dumps.
These helpers produce compact, readable renderings of the state fields the
nodes need to feed to the LLM.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_MAX_SNIPPET_CHARS = 240
_MAX_EVIDENCE_ITEMS = 10


def format_evidence(evidence: Iterable[dict[str, Any]] | None) -> str:
    if not evidence:
        return "_No evidence gathered._"
    items = list(evidence)
    lines: list[str] = []
    for i, item in enumerate(items[:_MAX_EVIDENCE_ITEMS], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "(untitled)").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        if len(snippet) > _MAX_SNIPPET_CHARS:
            snippet = snippet[:_MAX_SNIPPET_CHARS].rstrip() + "…"
        header = f"{i}. **{title}**"
        if url:
            header += f" — {url}"
        lines.append(header)
        if snippet:
            lines.append(f"   {snippet}")
    remaining = len(items) - _MAX_EVIDENCE_ITEMS
    if remaining > 0:
        lines.append(f"_({remaining} more item(s) omitted)_")
    return "\n".join(lines) if lines else "_No evidence gathered._"


def format_prior_rounds(rounds: Iterable[Any] | None) -> str:
    if not rounds:
        return ""
    # Group by round number, proponent first.
    by_round: dict[int, list[dict[str, Any]]] = {}
    for r in rounds:
        if not isinstance(r, dict):
            continue
        by_round.setdefault(int(r.get("round", 0)), []).append(r)
    parts: list[str] = []
    for rnum in sorted(by_round):
        entries = sorted(by_round[rnum], key=lambda e: 0 if e.get("side") == "proponent" else 1)
        parts.append(f"### Round {rnum}")
        for e in entries:
            side = str(e.get("side", "?")).title()
            argument = str(e.get("argument") or "").strip()
            parts.append(f"**{side}:** {argument}")
    return "\n".join(parts)
