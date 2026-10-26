"""Platform token contract v2 (docs/specs/10-platform-contract-v2.md).

Conformance cases 1-14 are copied from the workspace contract
(docs/specs/platform-contract-v2.md §3); C15-C27 are paper-trail's own. Every
case drives the composed app (``create_app()``: CORS, request ids, the token
middleware, the real routers) over ASGI, with only the debate service faked.
"""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from paper_trail.api import deps
from paper_trail.api.routers import debates as debates_router_mod
from paper_trail.core import rate_limit
from paper_trail.core.config import Settings, settings
from paper_trail.main import create_app
from paper_trail.platform import platform_token

SERVICE = "paper-trail"
BASTION_ENV = "BASTION_SIGNING_KEY_PUBLIC"
BASTION_URL_ENV = "BASTION_PUBLIC_KEY_URL"
FRONTEND_ENV = "PAPER_TRAIL_FRONTEND_SIGNING_KEY_PUBLIC"
DROP = object()  # a claim to leave out


# ─── keys and tokens ──────────────────────────────────────────────────────────


def _keypair() -> tuple[Ed25519PrivateKey, str]:
    """A fresh Ed25519 pair: the private key and the public half as base64 SPKI DER."""
    priv = Ed25519PrivateKey.generate()
    der = priv.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return priv, base64.b64encode(der).decode()


def _claims(minter: str, **overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    if minter == "bastion":
        claims: dict[str, Any] = {
            "iss": "bastion",
            "aud": SERVICE,
            "service": SERVICE,
            "sub": "v_abc1234567",
            "role": "viewer",
            "cap": "paper-trail.debate",
        }
    else:
        claims = {
            "iss": "paper-trail-frontend",
            "aud": SERVICE,
            "service": SERVICE,
            "sub": "paper-trail-frontend",
            "role": "frontend",
        }
    claims |= {"iat": now, "exp": now + 60, "jti": str(uuid.uuid4())}
    for name, value in overrides.items():
        if value is DROP:
            claims.pop(name, None)
        else:
            claims[name] = value
    return claims


def _sign(priv: Ed25519PrivateKey, claims: dict[str, Any]) -> str:
    return jwt.encode(claims, priv, algorithm="EdDSA", headers={"kid": "test-kid"})


def _b64url(data: dict[str, Any]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class Keys:
    """The two trusted keys, configured in the environment for one test."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.bastion, bastion_pub = _keypair()
        self.frontend, frontend_pub = _keypair()
        monkeypatch.setenv(BASTION_ENV, bastion_pub)
        monkeypatch.setenv(FRONTEND_ENV, frontend_pub)

    def bastion_token(self, **overrides: Any) -> str:
        return _sign(self.bastion, _claims("bastion", **overrides))

    def frontend_token(self, **overrides: Any) -> str:
        return _sign(self.frontend, _claims("frontend", **overrides))


# ─── the composed app with a fake debate service ──────────────────────────────


class _Debate:
    def __init__(self, debate_id: UUID) -> None:
        self.id = debate_id
        self.claim = "The Eiffel Tower is in Paris."
        self.max_rounds = 2
        self.status = "done"
        self.verdict = "TRUE"
        self.confidence = 0.95
        self.rounds: list[dict[str, Any]] = [
            {"side": "proponent", "round": 1, "argument": "a", "evidence": []}
        ]
        self.rounds_struct = [
            {"side": "proponent", "round": 1, "argument_md": "a", "citations": []}
        ]
        self.transcript_md = "# Debate: The Eiffel Tower is in Paris.\n\n## Verdict\n"
        self.transcript_hash = "f" * 64
        self.evidence_pool = None
        self.created_at = datetime.now(UTC)


class _FakeService:
    def __init__(self) -> None:
        self.debate = _Debate(uuid4())
        self.created: list[tuple[str, int]] = []

    async def create(
        self, claim: str, max_rounds: int, evidence_pool: list[dict[str, Any]] | None = None
    ) -> UUID:
        self.created.append((claim, max_rounds))
        return self.debate.id

    async def run(self, debate_id: UUID, *, entry: str = "api") -> Any:
        return self.debate

    async def get(self, debate_id: UUID) -> Any:
        return self.debate if debate_id == self.debate.id else None

    async def list(self, cursor: str | None, limit: int = 50) -> tuple[list[Any], str | None]:
        return [self.debate], None


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for var in (BASTION_ENV, BASTION_URL_ENV, FRONTEND_ENV):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    platform_token.reset_public_key_cache()
    # Alembic's fileConfig() in the migration tests disables loggers made before it.
    monkeypatch.setattr(platform_token.logger, "disabled", False)
    yield
    platform_token.reset_public_key_cache()


@pytest.fixture
def service() -> _FakeService:
    return _FakeService()


@pytest.fixture
def app(service: _FakeService, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    application = create_app()

    async def _override() -> AsyncIterator[_FakeService]:
        yield service

    async def _no_background(debate_id: UUID) -> None:
        return None

    application.dependency_overrides[deps.get_service] = _override
    monkeypatch.setattr(debates_router_mod, "_run_debate_background", _no_background)
    return application


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> Keys:
    return Keys(monkeypatch)


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _get_protected(app: FastAPI, token: str | None = None) -> Any:
    headers = {"X-Platform-Token": token} if token is not None else {}
    async with _client(app) as c:
        return await c.get("/debates", headers=headers)


def _is_invalid(response: Any) -> bool:
    return bool(
        response.status_code == 401 and response.json() == {"detail": "invalid_platform_token"}
    )


def _is_missing(response: Any) -> bool:
    return bool(
        response.status_code == 401 and response.json() == {"detail": "missing_platform_token"}
    )


# ─── conformance tests 1-14 (workspace contract §3) ───────────────────────────


async def test_01_valid_bastion_token_with_own_aud_is_authenticated(
    app: FastAPI, keys: Keys
) -> None:
    assert (await _get_protected(app, keys.bastion_token())).status_code == 200


async def test_02_aud_naming_another_service_is_invalid(app: FastAPI, keys: Keys) -> None:
    for other in ("magpie", "inkprint", "inkthread", "slowquery", "feathers-demo"):
        assert _is_invalid(await _get_protected(app, keys.bastion_token(aud=other))), other
        assert _is_invalid(await _get_protected(app, keys.frontend_token(aud=other))), other


async def test_03_aud_missing_or_a_list_is_invalid(app: FastAPI, keys: Keys) -> None:
    assert _is_invalid(await _get_protected(app, keys.bastion_token(aud=DROP)))
    assert _is_invalid(await _get_protected(app, keys.bastion_token(aud=[SERVICE])))
    assert _is_invalid(await _get_protected(app, keys.frontend_token(aud=[SERVICE, "magpie"])))


async def test_04_bastion_iss_signed_with_the_frontend_key_is_invalid(
    app: FastAPI, keys: Keys
) -> None:
    forged = _sign(keys.frontend, _claims("bastion"))
    assert _is_invalid(await _get_protected(app, forged))


async def test_05_frontend_key_token_with_another_role_is_invalid(app: FastAPI, keys: Keys) -> None:
    for role in ("admin", "editor", "viewer", ""):
        assert _is_invalid(await _get_protected(app, keys.frontend_token(role=role))), role
    assert _is_invalid(await _get_protected(app, keys.frontend_token(role=DROP)))


async def test_06_expiry_has_ten_seconds_of_leeway(app: FastAPI, keys: Keys) -> None:
    now = int(time.time())
    five_ago = keys.bastion_token(iat=now - 65, exp=now - 5)
    fifteen_ago = keys.bastion_token(iat=now - 75, exp=now - 15)
    assert (await _get_protected(app, five_ago)).status_code == 200
    assert _is_invalid(await _get_protected(app, fifteen_ago))


async def test_07_iat_in_the_future_has_ten_seconds_of_leeway(app: FastAPI, keys: Keys) -> None:
    now = int(time.time())
    five_ahead = keys.frontend_token(iat=now + 5, exp=now + 65)
    fifteen_ahead = keys.frontend_token(iat=now + 15, exp=now + 75)
    assert (await _get_protected(app, five_ahead)).status_code == 200
    assert _is_invalid(await _get_protected(app, fifteen_ahead))


async def test_08_alg_none_or_hs256_is_invalid(app: FastAPI, keys: Keys) -> None:
    claims = _claims("bastion")
    unsigned = f"{_b64url({'alg': 'none', 'typ': 'JWT'})}.{_b64url(claims)}."
    hs256 = jwt.encode(claims, "a-shared-secret-of-at-least-32-bytes!", algorithm="HS256")
    assert _is_invalid(await _get_protected(app, unsigned))
    assert _is_invalid(await _get_protected(app, hs256))


async def test_09_tampered_payload_is_invalid(app: FastAPI, keys: Keys) -> None:
    header, _, signature = keys.frontend_token().split(".")
    forged_claims = _b64url(_claims("frontend", sub="someone-else"))
    assert _is_invalid(await _get_protected(app, f"{header}.{forged_claims}.{signature}"))


async def test_10_missing_token_is_401_unless_demo_mode_which_logs_a_warning(
    app: FastAPI,
    keys: Keys,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert _is_missing(await _get_protected(app))

    monkeypatch.setattr(settings, "demo_mode", True)
    caplog.set_level(logging.WARNING, logger=platform_token.logger.name)
    assert (await _get_protected(app)).status_code == 200
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("DEMO_MODE" in r.getMessage() for r in warnings)


async def test_11_present_but_invalid_token_with_demo_mode_is_still_invalid(
    app: FastAPI, keys: Keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "demo_mode", True)
    assert _is_invalid(await _get_protected(app, keys.bastion_token(aud="magpie")))
    assert _is_invalid(await _get_protected(app, "not-a-token"))


async def test_12_no_trusted_key_outside_tests_fails_closed_but_health_answers(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    response = await _get_protected(app)
    assert response.status_code == 503
    assert response.json() == {"detail": "platform_auth_unconfigured"}
    # Even a token cannot open a route when nothing can verify it.
    stray, _ = _keypair()
    assert (await _get_protected(app, _sign(stray, _claims("bastion")))).status_code == 503
    async with _client(app) as c:
        assert (await c.get("/health")).status_code == 200


async def test_13_every_public_route_answers_without_a_token(
    app: FastAPI, keys: Keys, service: _FakeService, monkeypatch: pytest.MonkeyPatch
) -> None:
    signing_pem = (
        Ed25519PrivateKey.generate()
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    monkeypatch.setattr(settings, "transcript_signing_key", signing_pem)
    debate = service.debate.id
    public = [
        "/health",
        "/version",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/openapi.json",
        "/metrics",
        "/platform/receipt-public-key",
        f"/debates/{debate}/transcript.json",
        f"/debates/{debate}/transcript.md",
    ]
    async with _client(app) as c:
        for path in public:
            response = await c.get(path)
            assert response.status_code == 200, (path, response.status_code)
        # HEAD passes the token check too; FastAPI's GET routes then answer 405.
        head = await c.head(f"/debates/{debate}/transcript.md")
        assert head.status_code == 405


async def _buckets(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, requests: list[dict[str, str]]
) -> list[str]:
    """POST /debates once per header set; return the rate-limit identifier each one used."""
    seen: list[str] = []

    async def _capture(identifier: str, *, scope: str) -> None:
        assert scope == "debates"
        seen.append(identifier)

    monkeypatch.setattr(rate_limit, "enforce_rate_limit", _capture)
    async with _client(app) as c:
        for headers in requests:
            response = await c.post(
                "/debates", json={"claim": "x", "max_rounds": 2}, headers=headers
            )
            assert response.status_code == 201, response.text
    return seen


async def test_14_bastion_buckets_on_sub_and_frontend_visitors_on_their_address(
    app: FastAPI, keys: Keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    frontend = keys.frontend_token()
    seen = await _buckets(
        app,
        monkeypatch,
        [
            {"X-Platform-Token": keys.bastion_token(), "X-Forwarded-For": "203.0.113.5"},
            {"X-Platform-Token": frontend, "X-Forwarded-For": "203.0.113.5"},
            {"X-Platform-Token": frontend, "X-Forwarded-For": "198.51.100.7"},
        ],
    )
    assert seen == [
        "bastion:v_abc1234567",
        "paper-trail-frontend:203.0.113.5",
        "paper-trail-frontend:198.51.100.7",
    ]
    # Two visitors through the same frontend land in different buckets.
    assert seen[1] != seen[2]


# ─── paper-trail's own cases ──────────────────────────────────────────────────


async def test_c15_valid_frontend_token_and_any_bastion_role_are_authenticated(
    app: FastAPI, keys: Keys
) -> None:
    assert (await _get_protected(app, keys.frontend_token())).status_code == 200
    for role in ("viewer", "editor", "admin", "frontend"):
        assert (await _get_protected(app, keys.bastion_token(role=role))).status_code == 200


async def test_c16_frontend_iss_signed_with_bastions_key_is_invalid(
    app: FastAPI, keys: Keys
) -> None:
    forged = _sign(keys.bastion, _claims("frontend"))
    assert _is_invalid(await _get_protected(app, forged))


async def test_c17_lifetime_is_at_most_300_seconds(app: FastAPI, keys: Keys) -> None:
    now = int(time.time())
    assert (await _get_protected(app, keys.bastion_token(iat=now, exp=now + 300))).status_code == (
        200
    )
    assert _is_invalid(await _get_protected(app, keys.bastion_token(iat=now, exp=now + 301)))


async def test_c18_unknown_key_missing_sub_or_non_integer_times_are_invalid(
    app: FastAPI, keys: Keys
) -> None:
    stranger, _ = _keypair()
    now = int(time.time())
    assert _is_invalid(await _get_protected(app, _sign(stranger, _claims("bastion"))))
    assert _is_invalid(await _get_protected(app, keys.bastion_token(sub=DROP)))
    assert _is_invalid(await _get_protected(app, keys.bastion_token(sub="")))
    assert _is_invalid(await _get_protected(app, keys.bastion_token(exp=DROP)))
    assert _is_invalid(await _get_protected(app, keys.bastion_token(iat=DROP)))
    assert _is_invalid(await _get_protected(app, keys.bastion_token(exp=str(now + 60))))
    assert _is_invalid(await _get_protected(app, keys.bastion_token(iat=True)))


async def test_c19_the_warning_names_the_check_and_never_the_token(
    app: FastAPI, keys: Keys, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=platform_token.logger.name)
    token = keys.bastion_token(aud="magpie")
    assert _is_invalid(await _get_protected(app, token))
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("aud" in m for m in messages)
    assert token not in caplog.text
    assert token.split(".")[2] not in caplog.text


async def test_c20_the_public_list_is_method_aware_and_exact(
    app: FastAPI, keys: Keys, service: _FakeService
) -> None:
    debate = service.debate.id
    async with _client(app) as c:
        assert _is_missing(await c.post(f"/debates/{debate}/transcript.json"))
        assert _is_missing(await c.post("/platform/receipt-public-key"))
        assert _is_missing(await c.get(f"/debates/{debate}"))
        assert _is_missing(await c.get(f"/debates/{debate}/stream"))
        assert _is_missing(await c.get(f"/debates/{debate}/transcript.json/extra"))
        assert _is_missing(await c.post("/debates", json={"claim": "x"}))


async def test_c21_x_platform_token_is_read_before_a_bearer(app: FastAPI, keys: Keys) -> None:
    good, bad = keys.bastion_token(), keys.bastion_token(aud="magpie")
    async with _client(app) as c:
        bearer_only = await c.get("/debates", headers={"Authorization": f"Bearer {good}"})
        header_wins = await c.get(
            "/debates", headers={"X-Platform-Token": bad, "Authorization": f"Bearer {good}"}
        )
        not_bearer = await c.get("/debates", headers={"Authorization": f"Basic {good}"})
    assert bearer_only.status_code == 200
    assert _is_invalid(header_wins)
    assert _is_missing(not_bearer)


async def test_c22_platform_debate_is_bastion_only(
    app: FastAPI, keys: Keys, service: _FakeService, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = {"claim": "The Eiffel Tower is in Paris.", "max_rounds": 2}
    async with _client(app) as c:
        bastion = await c.post(
            "/platform/debate",
            json=body,
            headers={"Authorization": f"Bearer {keys.bastion_token()}"},
        )
        frontend = await c.post(
            "/platform/debate", json=body, headers={"X-Platform-Token": keys.frontend_token()}
        )
        monkeypatch.setattr(settings, "demo_mode", True)
        anonymous = await c.post("/platform/debate", json=body)
    assert bastion.status_code == 200
    assert bastion.json()["verdict"] == "TRUE"
    assert _is_invalid(frontend)
    assert _is_missing(anonymous)
    assert service.created == [("The Eiffel Tower is in Paris.", 2)]


async def test_c23_test_settings_without_a_key_pass_as_anonymous(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (await _get_protected(app)).status_code == 200
    for env in ("production", "development", ""):
        monkeypatch.setattr(settings, "app_env", env)
        assert (await _get_protected(app)).status_code == 503, env


async def test_c24_anonymous_and_frontend_buckets_use_the_client_address(
    app: FastAPI, keys: Keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    frontend = keys.frontend_token()
    seen = await _buckets(
        app,
        monkeypatch,
        [
            {"X-Platform-Token": frontend, "X-Forwarded-For": "203.0.113.5, 10.0.0.1"},
            {"X-Platform-Token": frontend},
        ],
    )
    # The leftmost entry of a chain; without the header, the socket peer.
    assert seen == ["paper-trail-frontend:203.0.113.5", "paper-trail-frontend:127.0.0.1"]


async def test_c24_anonymous_requests_bucket_on_the_forwarded_address(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = await _buckets(app, monkeypatch, [{"X-Forwarded-For": "203.0.113.5, 10.0.0.1"}])
    assert seen == ["203.0.113.5"]


async def test_c25_cors_wraps_the_token_check(
    service: _FakeService, keys: Keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = "https://paper-trail-frontend-sable.vercel.app"
    monkeypatch.setattr(settings, "cors_origins", origin)
    application = create_app()

    async def _override() -> AsyncIterator[_FakeService]:
        yield service

    application.dependency_overrides[deps.get_service] = _override
    async with _client(application) as c:
        preflight = await c.options(
            "/debates",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type, x-platform-token, x-request-id",
            },
        )
        rejected = await c.get("/debates", headers={"Origin": origin})
    assert preflight.status_code == 200
    allowed = preflight.headers["access-control-allow-headers"].lower()
    for header in ("x-platform-token", "x-request-id", "content-type"):
        assert header in allowed
    assert _is_missing(rejected)
    assert rejected.headers["access-control-allow-origin"] == origin
    assert rejected.headers.get("x-request-id")


async def test_c26_key_values_are_cleaned_and_malformed_ones_are_absent(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    frontend, frontend_pub = _keypair()
    monkeypatch.setenv(FRONTEND_ENV, f"﻿{frontend_pub}\r\n")
    assert (await _get_protected(app, _sign(frontend, _claims("frontend")))).status_code == 200

    p256 = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    caplog.set_level(logging.WARNING, logger=platform_token.logger.name)
    for malformed in (
        "not base64 at all!!",
        base64.b64encode(b"not a key").decode(),
        base64.b64encode(p256).decode(),
    ):
        caplog.clear()
        platform_token.reset_public_key_cache()
        monkeypatch.setenv(FRONTEND_ENV, malformed)
        monkeypatch.setenv(BASTION_ENV, malformed)
        monkeypatch.setattr(settings, "app_env", "production")
        # Both keys malformed = no trusted key: fail closed, never crash.
        assert (await _get_protected(app)).status_code == 503
        assert (await _get_protected(app)).status_code == 503
        named = [r for r in caplog.records if FRONTEND_ENV in r.getMessage()]
        assert len(named) == 1, "one warning per malformed value, not one per request"
        assert malformed not in caplog.text


async def test_c26_bastions_url_key_is_fetched_once_and_cached(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    bastion, bastion_pub = _keypair()
    monkeypatch.setenv(BASTION_URL_ENV, "https://bastion.example/api/public-key")
    calls: list[str] = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"kid": "k", "algorithm": "EdDSA", "publicKey": bastion_pub}

    def _fake_get(url: str, timeout: float) -> _Response:
        calls.append(url)
        return _Response()

    monkeypatch.setattr(platform_token.httpx, "get", _fake_get)
    token = _sign(bastion, _claims("bastion"))
    assert (await _get_protected(app, token)).status_code == 200
    assert (await _get_protected(app, token)).status_code == 200
    assert calls == ["https://bastion.example/api/public-key"]


def test_c27_demo_mode_defaults_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEMO_MODE", raising=False)
    assert Settings(_env_file=None).demo_mode is False  # type: ignore[call-arg]
