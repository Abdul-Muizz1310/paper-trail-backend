"""Ed25519 signing of transcript receipts.

A bare content hash proves integrity but not provenance — anyone can recompute
it. To back the product's "receipt" claim, the service signs the canonical
transcript JSON with a service-held Ed25519 private key (`TRANSCRIPT_SIGNING_KEY`,
PEM) and publishes the corresponding public key so a third party can verify a
transcript was produced by this service and not tampered with.

When no signing key is configured the receipt carries only the hash and no
signature (the API surfaces `signature: null`) — the claim is scoped honestly
rather than faked.
"""

from __future__ import annotations

import base64
import logging

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from paper_trail.core.config import settings

logger = logging.getLogger(__name__)

SIGNATURE_ALG = "Ed25519"


def _load_private_key() -> Ed25519PrivateKey | None:
    pem = settings.transcript_signing_key
    if not pem:
        return None
    try:
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except (ValueError, TypeError):
        logger.warning("invalid TRANSCRIPT_SIGNING_KEY; transcripts will be unsigned")
        return None
    if not isinstance(key, Ed25519PrivateKey):
        logger.warning("TRANSCRIPT_SIGNING_KEY is not an Ed25519 key; transcripts unsigned")
        return None
    return key


def sign_transcript(canonical: str) -> str | None:
    """Return the base64 Ed25519 signature over `canonical`, or None if unsigned."""
    key = _load_private_key()
    if key is None:
        return None
    return base64.b64encode(key.sign(canonical.encode("utf-8"))).decode("ascii")


def public_key_pem() -> str | None:
    """Return the PEM-encoded Ed25519 public key, or None when unsigned."""
    key = _load_private_key()
    if key is None:
        return None
    return (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )


def verify_transcript_signature(canonical: str, signature_b64: str, public_pem: str) -> bool:
    """Verify a base64 Ed25519 signature over `canonical` against `public_pem`."""
    try:
        pub = serialization.load_pem_public_key(public_pem.encode("utf-8"))
        if not isinstance(pub, Ed25519PublicKey):
            return False
        pub.verify(base64.b64decode(signature_b64), canonical.encode("utf-8"))
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True
