"""Shared formatters for node user-message construction.

Small models parse structured markdown more reliably than Python-repr dumps.
These helpers produce compact, readable renderings of the state fields the
nodes need to feed to the LLM.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_MAX_SNIPPET_CHARS = 240
# When an evidence item carries full extracted article text (fetched by the
# plan node), we can afford to surface more of it than a bare Tavily snippet.
_MAX_ARTICLE_CHARS = 800
_MAX_EVIDENCE_ITEMS = 10
# Pool items can carry full text bodies (vs evidence hits' snippets), so
# trim a little harder when formatting into LLM context.
_MAX_POOL_TEXT_CHARS = 400
_MAX_POOL_ITEMS = 50


def format_evidence_pool(pool: Iterable[dict[str, Any]] | None) -> str:
    """Render the caller-supplied evidence pool for LLM context.

    Each item is keyed by its `[cert:<uuid>]` marker so the LLM has an
    unambiguous way to cite it. Items with a missing/invalid
    `certificate_id` are skipped.
    """
    if not pool:
        return ""
    items = list(pool)
    lines: list[str] = []
    for item in items[:_MAX_POOL_ITEMS]:
        if not isinstance(item, dict):
            continue
        cid = item.get("certificate_id")
        if not cid:
            continue
        title = str(item.get("title") or "(untitled)").strip()
        url = str(item.get("url") or "").strip()
        text = str(item.get("text") or "").strip()
        if len(text) > _MAX_POOL_TEXT_CHARS:
            text = text[:_MAX_POOL_TEXT_CHARS].rstrip() + "…"
        header = f"- [cert:{cid}] **{title}**"
        if url:
            header += f" — {url}"
        lines.append(header)
        if text:
            lines.append(f"   {text}")
    remaining = len(items) - _MAX_POOL_ITEMS
    if remaining > 0:
        lines.append(f"_({remaining} more pool item(s) omitted)_")
    return "\n".join(lines)


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
        # Prefer full extracted article text (grounds arguments in the source
        # body, not just Tavily's ~1-2 sentence snippet) when the plan node
        # fetched it; otherwise fall back to the snippet.
        article = str(item.get("text") or "").strip()
        if article:
            body, limit = article, _MAX_ARTICLE_CHARS
        else:
            body, limit = str(item.get("snippet") or "").strip(), _MAX_SNIPPET_CHARS
        if len(body) > limit:
            body = body[:limit].rstrip() + "…"
        header = f"{i}. **{title}**"
        if url:
            header += f" — {url}"
        lines.append(header)
        if body:
            lines.append(f"   {body}")
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
