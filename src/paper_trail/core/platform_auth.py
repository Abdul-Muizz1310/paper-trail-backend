"""Platform token verification (Ed25519 JWT). Demo mode stub for v0.1."""

from __future__ import annotations

from paper_trail.core.config import settings


def verify_platform_token(token: str | None) -> bool:
    """Verify a platform token.

    In demo mode, any value (including None) is accepted. Real Ed25519 JWT
    verification is deferred to v0.2 — the stub rejects everything when
    demo_mode is False.
    """
    return bool(settings.demo_mode)
