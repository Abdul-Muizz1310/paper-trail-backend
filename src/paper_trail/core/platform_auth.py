"""Platform token verification (Ed25519 JWT). Demo mode stub for v0.1."""

from __future__ import annotations

from paper_trail.core.config import settings


def verify_platform_token(token: str | None) -> bool:
    raise NotImplementedError
