"""Citation resolution for proponent / skeptic round entries.

Pure functions — given an argument string, the caller-supplied evidence pool,
and the Tavily-sourced evidence hits, produce the typed citation list that
ships inside the round entry.

Invariants:
- A `[cert:<uuid>]` marker in the argument only becomes a citation if the
  UUID exists in the pool. Invented cert refs are dropped.
- Tavily-sourced URL citations are emitted only when the argument text
  literally contains the URL.
- The returned list is ordered (cert markers in the order they appear,
  then URL mentions in pool-evidence-list order) and de-duplicated.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from paper_trail.agents.tools.transcript import extract_cert_markers


def _index_pool_by_cert(pool: list[dict[str, Any]]) -> dict[UUID, dict[str, Any]]:
    """Build a `{UUID: item}` map; items with a missing/invalid id are skipped."""
    out: dict[UUID, dict[str, Any]] = {}
    for item in pool:
        if not isinstance(item, dict):
            continue
        raw = item.get("certificate_id")
        if raw is None:
            continue
        try:
            uid = UUID(str(raw))
        except (ValueError, TypeError):
            continue
        out[uid] = item
    return out


def build_round_citations(
    argument: str,
    *,
    pool: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve `[cert:<uuid>]` markers and inline URL mentions into citations.

    Output shape (match schemas.debates.Citation):
        {"type": "cert" | "url", "ref": str, "title": str}
    """
    citations: list[dict[str, Any]] = []
    seen_refs: set[tuple[str, str]] = set()

    if pool:
        pool_index = _index_pool_by_cert(pool)
        # `extract_cert_markers` already de-dupes UUIDs, so we don't need a
        # second-pass dedup here.
        for uid in extract_cert_markers(argument):
            item = pool_index.get(uid)
            if item is None:
                # Never invent cert refs — a marker that doesn't match a
                # pool item is silently dropped.
                continue
            seen_refs.add(("cert", str(uid)))
            citations.append(
                {
                    "type": "cert",
                    "ref": str(uid),
                    "title": str(item.get("title") or ""),
                }
            )

    if evidence:
        for hit in evidence:
            if not isinstance(hit, dict):
                continue
            url = str(hit.get("url") or "").strip()
            if not url or url not in argument:
                continue
            key = ("url", url)
            if key in seen_refs:
                continue
            seen_refs.add(key)
            citations.append(
                {
                    "type": "url",
                    "ref": url,
                    "title": str(hit.get("title") or url),
                }
            )

    return citations
