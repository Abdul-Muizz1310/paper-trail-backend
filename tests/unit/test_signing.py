"""Unit tests for core/signing.py — Ed25519 transcript receipts."""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from paper_trail.core import signing
from paper_trail.core.config import settings


def _ed25519_pem() -> str:
    key = Ed25519PrivateKey.generate()
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def test_unconfigured_yields_no_signature(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "transcript_signing_key", "")
    assert signing.sign_transcript("payload") is None
    assert signing.public_key_pem() is None


def test_sign_and_verify_roundtrip(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "transcript_signing_key", _ed25519_pem())
    sig = signing.sign_transcript("the canonical payload")
    assert sig is not None
    pub = signing.public_key_pem()
    assert pub is not None
    assert signing.verify_transcript_signature("the canonical payload", sig, pub) is True
    # Tampered payload → verification fails.
    assert signing.verify_transcript_signature("tampered", sig, pub) is False


def test_invalid_pem_is_unsigned(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "transcript_signing_key", "-----BEGIN nonsense-----")
    assert signing.sign_transcript("x") is None
    assert signing.public_key_pem() is None


def test_non_ed25519_key_rejected(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = rsa_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    monkeypatch.setattr(settings, "transcript_signing_key", pem)
    assert signing.sign_transcript("x") is None


def test_verify_handles_garbage_inputs() -> None:
    assert signing.verify_transcript_signature("x", "!!not-base64!!", "not a pem") is False
    assert signing.verify_transcript_signature("x", "aGk=", "not a pem") is False
