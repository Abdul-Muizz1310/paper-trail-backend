"""Unit tests for core/platform_auth.py."""

from __future__ import annotations

import base64
import datetime as dt
import time

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


_FRONTEND_ENV = "PAPER_TRAIL_FRONTEND_SIGNING_KEY_PUBLIC"


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv(_FRONTEND_ENV, raising=False)
    platform_token.reset_public_key_cache()


# ─── /platform/debate stays bastion-only (docs/specs/09-platform-token.md) ────


def _frontend_token(priv_pem: str) -> str:
    """A valid paper-trail-frontend token: right service, right role, live exp."""
    return jwt.encode(
        {
            "sub": "paper-trail-frontend",
            "role": "frontend",
            "service": "paper-trail",
            "exp": dt.datetime.now(dt.UTC) + dt.timedelta(seconds=60),
        },
        priv_pem,
        algorithm="EdDSA",
    )


def test_frontend_token_rejected_by_platform_verifier(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A frontend-key token is never a bearer for /platform/debate."""
    monkeypatch.setattr(settings, "demo_mode", False)
    bastion_priv, bastion_pub = _keypair()
    frontend_priv, frontend_pub = _keypair()
    monkeypatch.setenv("BASTION_SIGNING_KEY_PUBLIC", bastion_pub)
    monkeypatch.setenv(_FRONTEND_ENV, frontend_pub)
    monkeypatch.delenv("BASTION_PUBLIC_KEY_URL", raising=False)
    platform_token.reset_public_key_cache()

    assert platform_auth.verify_platform_token(_frontend_token(frontend_priv)) is False
    bastion = jwt.encode(
        {"sub": "bastion", "exp": dt.datetime.now(dt.UTC) + dt.timedelta(seconds=60)},
        bastion_priv,
        algorithm="EdDSA",
    )
    assert platform_auth.verify_platform_token(bastion) is True


def test_only_frontend_key_platform_verifier_fails_closed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "demo_mode", False)
    frontend_priv, frontend_pub = _keypair()
    monkeypatch.delenv("BASTION_SIGNING_KEY_PUBLIC", raising=False)
    monkeypatch.delenv("BASTION_PUBLIC_KEY_URL", raising=False)
    monkeypatch.setenv(_FRONTEND_ENV, frontend_pub)
    platform_token.reset_public_key_cache()
    assert platform_auth.verify_platform_token(_frontend_token(frontend_priv)) is False


def test_bastion_token_without_exp_rejected_by_platform_verifier(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "demo_mode", False)
    bastion_priv, bastion_pub = _keypair()
    monkeypatch.setenv("BASTION_SIGNING_KEY_PUBLIC", bastion_pub)
    monkeypatch.delenv("BASTION_PUBLIC_KEY_URL", raising=False)
    platform_token.reset_public_key_cache()
    no_exp = jwt.encode({"sub": "bastion"}, bastion_priv, algorithm="EdDSA")
    assert platform_auth.verify_platform_token(no_exp) is False


# (iat offset, exp offset, accepted) — the 10 s clock-skew leeway, spec 09.
_SKEW_CASES = {
    "iat-5s-ahead": (5, 65, True),
    "iat-30s-ahead": (30, 90, False),
    "exp-5s-ago": (-65, -5, True),
    "exp-30s-ago": (-90, -30, False),
}


@pytest.mark.parametrize(
    ("iat_offset_s", "exp_offset_s", "accepted"), _SKEW_CASES.values(), ids=_SKEW_CASES.keys()
)
def test_platform_verifier_clock_skew_leeway(  # type: ignore[no-untyped-def]
    monkeypatch, iat_offset_s: int, exp_offset_s: int, accepted: bool
) -> None:
    """The /platform/debate bearer check shares the 10 s leeway on iat and exp."""
    monkeypatch.setattr(settings, "demo_mode", False)
    bastion_priv, bastion_pub = _keypair()
    monkeypatch.setenv("BASTION_SIGNING_KEY_PUBLIC", bastion_pub)
    monkeypatch.delenv("BASTION_PUBLIC_KEY_URL", raising=False)
    platform_token.reset_public_key_cache()
    now = int(time.time())
    token = jwt.encode(
        {"sub": "bastion", "iat": now + iat_offset_s, "exp": now + exp_offset_s},
        bastion_priv,
        algorithm="EdDSA",
    )
    assert platform_auth.verify_platform_token(token) is accepted
