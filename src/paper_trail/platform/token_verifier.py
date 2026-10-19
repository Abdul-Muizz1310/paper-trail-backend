"""Platform token contract v2: the pure verifier (docs/specs/10-platform-contract-v2.md §2).

No I/O and no HTTP here: ``verify_token`` takes the token, the trusted keys and
the time, and returns the caller's ``Principal`` or raises ``TokenRejectedError``
naming the first check that failed. Loading keys and answering requests is
``platform_token.py``'s job.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# This backend's service id: a constant, never configuration (contract §1).
SERVICE_ID = "paper-trail"
BASTION_ISSUER = "bastion"
FRONTEND_ISSUER = "paper-trail-frontend"
FRONTEND_ROLE = "frontend"
ALGORITHM = "EdDSA"
LEEWAY_S = 10
MAX_LIFETIME_S = 300


@dataclass(frozen=True)
class TrustedKey:
    """One verification key and what a token it verifies must claim."""

    key: Ed25519PublicKey
    issuer: str
    required_role: str | None


@dataclass(frozen=True)
class Principal:
    """The verified caller of one request."""

    iss: str
    sub: str
    role: str
    aud: str

    @property
    def rate_limit_key(self) -> str:
        return f"{self.iss}:{self.sub}"


class TokenRejectedError(Exception):
    """A present token failed ``check`` (a name safe to log; never the token)."""

    def __init__(self, check: str) -> None:
        super().__init__(check)
        self.check = check


def _json_object(segment: bytes) -> dict[str, Any]:
    try:
        value = json.loads(segment)
    except (ValueError, UnicodeDecodeError) as exc:
        raise TokenRejectedError("malformed") from exc
    if not isinstance(value, dict):
        raise TokenRejectedError("malformed")
    return value


def _header(token: str) -> dict[str, Any]:
    if token.count(".") != 2:
        raise TokenRejectedError("malformed")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenRejectedError("malformed") from exc
    if not isinstance(header, dict):  # pragma: no cover - PyJWT already insists on an object
        raise TokenRejectedError("malformed")
    return header


def _signed_payload(token: str, keys: Sequence[TrustedKey]) -> tuple[TrustedKey, dict[str, Any]]:
    """The first trusted key whose signature verifies, with the payload it covers."""
    jws = jwt.PyJWS()
    for trusted in keys:
        try:
            decoded = jws.decode_complete(token, key=trusted.key, algorithms=[ALGORITHM])
        except jwt.InvalidSignatureError:
            continue
        except jwt.PyJWTError as exc:  # pragma: no cover - _header() already decoded every part
            raise TokenRejectedError("malformed") from exc
        return trusted, _json_object(decoded["payload"])
    raise TokenRejectedError("signature")


def _int_claim(claims: dict[str, Any], name: str) -> int:
    value = claims.get(name)
    # bool is an int subclass; a JSON `true` is not a time.
    if not isinstance(value, int) or isinstance(value, bool):
        raise TokenRejectedError(name)
    return value


def verify_token(token: str, keys: Sequence[TrustedKey], *, now: float) -> Principal:
    """Verify ``token`` against ``keys`` at ``now`` (contract v2 §2), in the spec's order."""
    if _header(token).get("alg") != ALGORITHM:
        raise TokenRejectedError("alg")
    trusted, claims = _signed_payload(token, keys)

    # The key decides which issuer is allowed, never the claim.
    if claims.get("iss") != trusted.issuer:
        raise TokenRejectedError("iss")
    role = claims.get("role", "")
    if not isinstance(role, str):
        raise TokenRejectedError("role")
    if trusted.required_role is not None and role != trusted.required_role:
        raise TokenRejectedError("role")

    aud = claims.get("aud")
    if not isinstance(aud, str) or aud != SERVICE_ID:
        raise TokenRejectedError("aud")

    exp = _int_claim(claims, "exp")
    iat = _int_claim(claims, "iat")
    if not exp + LEEWAY_S > now:
        raise TokenRejectedError("exp")
    if not iat - LEEWAY_S <= now:
        raise TokenRejectedError("iat")
    if exp - iat > MAX_LIFETIME_S:
        raise TokenRejectedError("lifetime")

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise TokenRejectedError("sub")
    return Principal(iss=trusted.issuer, sub=sub, role=role, aud=aud)
