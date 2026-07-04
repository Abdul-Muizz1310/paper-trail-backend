"""Unit tests for core/platform_auth.py."""

from __future__ import annotations

import base64
import datetime as dt

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from paper_trail.core import platform_auth
from paper_trail.core.config import settings
from paper_trail.platform import platform_token


def test_demo_mode_accepts_any(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "demo_mode", True)
    assert platform_auth.verify_platform_token(None) is True
    assert platform_auth.verify_platform_token("anything") is True


def test_non_demo_no_key_fails_closed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """No token, or a token with no configured key → rejected (fail closed)."""
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.delenv("BASTION_SIGNING_KEY_PUBLIC", raising=False)
    monkeypatch.delenv("BASTION_PUBLIC_KEY_URL", raising=False)
    platform_token.reset_public_key_cache()
    assert platform_auth.verify_platform_token(None) is False
    assert platform_auth.verify_platform_token("garbage") is False


def _keypair() -> tuple[str, str]:
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub_der = priv.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, base64.b64encode(pub_der).decode()


def test_non_demo_honors_valid_and_rejects_bad_token(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The token argument is actually verified against bastion's key."""
    monkeypatch.setattr(settings, "demo_mode", False)
    priv_pem, pub_b64 = _keypair()
    monkeypatch.setenv("BASTION_SIGNING_KEY_PUBLIC", pub_b64)
    monkeypatch.delenv("BASTION_PUBLIC_KEY_URL", raising=False)
    platform_token.reset_public_key_cache()

    good = jwt.encode(
        {"sub": "bastion", "exp": dt.datetime.now(dt.UTC) + dt.timedelta(seconds=60)},
        priv_pem,
        algorithm="EdDSA",
    )
    assert platform_auth.verify_platform_token(good) is True
    # A token signed by a *different* key must be rejected.
    other_priv, _ = _keypair()
    forged = jwt.encode(
        {"sub": "attacker", "exp": dt.datetime.now(dt.UTC) + dt.timedelta(seconds=60)},
        other_priv,
        algorithm="EdDSA",
    )
    assert platform_auth.verify_platform_token(forged) is False
    assert platform_auth.verify_platform_token("not-a-jwt") is False


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    platform_token.reset_public_key_cache()
