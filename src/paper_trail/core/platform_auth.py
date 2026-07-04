"""Platform token verification (Ed25519 JWT).

Demo mode accepts anything (local demos). Outside demo mode the bearer token
must be a valid EdDSA JWT verifiable against bastion's configured public key —
the function honors its `token` argument and fails **closed** when no key is
configured, so the paid `/platform/debate` endpoint is never open to any
non-empty string on a real deployment.
"""

from __future__ import annotations

from paper_trail.core.config import settings
from paper_trail.platform.platform_token import verify_platform_jwt


def verify_platform_token(token: str | None) -> bool:
    """Verify a platform bearer token.

    - Demo mode (`DEMO_MODE=true`): any value (including None) is accepted.
    - Otherwise: `token` must be a valid EdDSA JWT signed by bastion's key.
      Missing token, missing key, or bad signature → rejected.
    """
    if settings.demo_mode:
        return True
    if not token:
        return False
    return verify_platform_jwt(token)
