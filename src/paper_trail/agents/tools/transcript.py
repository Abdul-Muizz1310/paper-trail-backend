"""Pure helpers for structured-transcript hashing and cert-marker parsing.

These are deliberately side-effect-free so they can be unit-tested without
touching the LLM, DB, or HTTP layers. The render node and the
`transcript.json` endpoint both depend on them.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from uuid import UUID

# `[cert:<uuid>]` — case-insensitive on the literal "cert" prefix so the
# LLM has some wiggle room, but the UUID itself must parse strictly.
_CERT_MARKER_RE = re.compile(
    r"\[cert:(?P<uuid>[0-9a-fA-F-]{32,36})\]",
    re.IGNORECASE,
)


def extract_cert_markers(text: str) -> list[UUID]:
    """Return the ordered, de-duplicated list of cert UUIDs referenced in `text`.

    Any marker whose body isn't a valid UUID is silently dropped — we never
    invent or partially-match cert refs.
    """
    seen: set[UUID] = set()
    out: list[UUID] = []
    for match in _CERT_MARKER_RE.finditer(text):
        try:
            uid = UUID(match.group("uuid"))
        except ValueError:
            continue
        if uid in seen:
            continue
        seen.add(uid)
        out.append(uid)
    return out


def canonical_transcript_json(
    *,
    claim: str,
    verdict: str,
    confidence: float,
    rounds: list[dict[str, Any]],
) -> str:
    """Serialize the transcript payload deterministically.

    `sort_keys=True` + `separators=(",", ":")` + `ensure_ascii=False` gives
    the same byte string for the same logical payload regardless of dict
    insertion order or unicode handling. This is the exact bytes we hash.
    """
    payload = {
        "claim": claim,
        "verdict": verdict,
        "confidence": confidence,
        "rounds": rounds,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_transcript(
    *,
    claim: str,
    verdict: str,
    confidence: float,
    rounds: list[dict[str, Any]],
) -> str:
    """Hex SHA-256 over the canonical transcript JSON (64 chars)."""
    canonical = canonical_transcript_json(
        claim=claim,
        verdict=verdict,
        confidence=confidence,
        rounds=rounds,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
