"""The pure verifier of spec 10 §2: one named rejection per failed check."""

from __future__ import annotations

import base64
import json
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from paper_trail.platform.token_verifier import (
    BASTION_ISSUER,
    FRONTEND_ISSUER,
    SERVICE_ID,
    Principal,
    TokenRejectedError,
    TrustedKey,
    verify_token,
)

NOW = 1_760_000_000
BASTION = Ed25519PrivateKey.generate()
FRONTEND = Ed25519PrivateKey.generate()
KEYS = (
    TrustedKey(key=BASTION.public_key(), issuer=BASTION_ISSUER, required_role=None),
    TrustedKey(key=FRONTEND.public_key(), issuer=FRONTEND_ISSUER, required_role="frontend"),
)


def _claims(**overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "iss": BASTION_ISSUER,
        "aud": SERVICE_ID,
        "sub": "v_abc1234567",
        "role": "editor",
        "iat": NOW,
        "exp": NOW + 60,
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def _token(claims: dict[str, Any], key: Ed25519PrivateKey = BASTION) -> str:
    return jwt.encode(claims, key, algorithm="EdDSA")


def _part(data: Any) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def _rejection(token: str) -> str:
    with pytest.raises(TokenRejectedError) as exc:
        verify_token(token, KEYS, now=NOW)
    return exc.value.check


def test_constants_name_this_service() -> None:
    assert (SERVICE_ID, BASTION_ISSUER, FRONTEND_ISSUER) == (
        "paper-trail",
        "bastion",
        "paper-trail-frontend",
    )


def test_a_valid_token_yields_its_principal() -> None:
    assert verify_token(_token(_claims()), KEYS, now=NOW) == Principal(
        iss="bastion", sub="v_abc1234567", role="editor", aud="paper-trail"
    )


def test_a_frontend_token_yields_the_frontend_principal() -> None:
    claims = _claims(iss=FRONTEND_ISSUER, sub="paper-trail-frontend", role="frontend")
    principal = verify_token(_token(claims, FRONTEND), KEYS, now=NOW)
    assert (principal.iss, principal.sub) == ("paper-trail-frontend", "paper-trail-frontend")


def test_a_bastion_token_without_role_has_an_empty_role() -> None:
    assert verify_token(_token(_claims(role=None)), KEYS, now=NOW).role == ""


@pytest.mark.parametrize(
    "token",
    [
        "",
        "only-one-part",
        "a.b",
        "a.b.c.d",
        f"{_part(['not', 'an', 'object'])}.{_part({})}.sig",
        "!!!.???.###",
    ],
    ids=["empty", "one-part", "two-parts", "four-parts", "header-not-object", "not-base64"],
)
def test_malformed_tokens(token: str) -> None:
    assert _rejection(token) in {"malformed", "alg"}


def test_a_payload_that_is_not_an_object_is_malformed() -> None:
    signed = jwt.api_jws.encode(json.dumps([1, 2]).encode(), BASTION, algorithm="EdDSA")
    assert _rejection(signed) == "malformed"


@pytest.mark.parametrize(
    ("overrides", "key", "check"),
    [
        ({"iss": FRONTEND_ISSUER}, BASTION, "iss"),
        ({"iss": None}, BASTION, "iss"),
        ({"role": "admin", "iss": FRONTEND_ISSUER}, FRONTEND, "role"),
        ({"role": 5}, BASTION, "role"),
        ({"aud": "magpie"}, BASTION, "aud"),
        ({"aud": None}, BASTION, "aud"),
        ({"aud": [SERVICE_ID]}, BASTION, "aud"),
        ({"exp": None}, BASTION, "exp"),
        ({"exp": NOW - 10}, BASTION, "exp"),
        ({"exp": 1.5e9}, BASTION, "exp"),
        ({"iat": None}, BASTION, "iat"),
        ({"iat": NOW + 11}, BASTION, "iat"),
        ({"iat": False}, BASTION, "iat"),
        ({"exp": NOW + 301}, BASTION, "lifetime"),
        ({"sub": None}, BASTION, "sub"),
        ({"sub": 7}, BASTION, "sub"),
    ],
)
def test_each_failed_check_is_named(
    overrides: dict[str, Any], key: Ed25519PrivateKey, check: str
) -> None:
    assert _rejection(_token(_claims(**overrides), key)) == check


def test_the_boundaries_of_the_leeway_and_the_lifetime() -> None:
    assert verify_token(_token(_claims(exp=NOW - 9, iat=NOW - 69)), KEYS, now=NOW)
    assert verify_token(_token(_claims(iat=NOW + 10, exp=NOW + 70)), KEYS, now=NOW)
    assert verify_token(_token(_claims(exp=NOW + 300)), KEYS, now=NOW)


def test_an_unknown_key_or_no_key_is_a_signature_rejection() -> None:
    stranger = Ed25519PrivateKey.generate()
    assert _rejection(_token(_claims(), stranger)) == "signature"
    with pytest.raises(TokenRejectedError) as exc:
        verify_token(_token(_claims()), (), now=NOW)
    assert exc.value.check == "signature"


def test_other_algorithms_never_reach_a_key() -> None:
    hs = jwt.encode(_claims(), "a-shared-secret-of-at-least-32-bytes!", algorithm="HS256")
    unsigned = f"{_part({'alg': 'none'})}.{_part(_claims())}."
    assert _rejection(hs) == "alg"
    assert _rejection(unsigned) == "alg"


def test_a_signed_payload_that_is_not_json_is_malformed() -> None:
    signed = jwt.api_jws.encode(b"not json", BASTION, algorithm="EdDSA")
    assert _rejection(signed) == "malformed"


def test_an_undecodable_signature_segment_is_malformed() -> None:
    header, claims, _ = _token(_claims()).split(".")
    # One base64url character cannot be decoded at all (a bad padding length).
    assert _rejection(f"{header}.{claims}.A") == "malformed"
