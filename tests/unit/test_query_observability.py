"""Spec 11 (fast tier): slowquery-detective wired into the app, behind contract v2."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from slowquery_detective.tagging import RequestIdMiddleware
from starlette.requests import Request

from paper_trail.core.config import settings
from paper_trail.main import create_app
from paper_trail.platform import platform_token
from paper_trail.platform.query_observability import authorize_bastion, store_url_for
from paper_trail.platform.token_verifier import Principal
from tests.unit.test_platform_contract_v2 import BASTION_ENV, BASTION_URL_ENV, FRONTEND_ENV, Keys


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for var in (BASTION_ENV, BASTION_URL_ENV, FRONTEND_ENV):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "app_env", "test")
    platform_token.reset_public_key_cache()
    yield
    platform_token.reset_public_key_cache()


def _request(principal: Principal | None) -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    if principal is not None:
        request.state.platform_principal = principal
    return request


# ─── O1 wiring ───────────────────────────────────────────────────────────────


def test_o1_create_app_installs_the_library_once_and_outermost() -> None:
    app = create_app()
    assert app.state.slowquery_installed is True
    # The routes themselves answer in O4 (FastAPI keeps included routers unflattened).
    # Starlette runs user_middleware[0] outermost: the tagger wraps everything.
    assert app.user_middleware[0].cls is RequestIdMiddleware
    assert sum(m.cls is RequestIdMiddleware for m in app.user_middleware) == 1


# ─── O2 store URL ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "postgres://u:p@host/db",
        "postgresql://u:p@host/db",
        "postgresql+asyncpg://u:p@ep-x.neon.tech/db?ssl=require",
    ],
)
def test_o2_a_postgres_database_url_is_the_store_url(url: str) -> None:
    assert store_url_for(url) == url


@pytest.mark.parametrize("url", ["sqlite+aiosqlite:///:memory:", "sqlite:///x.db", "", "mysql://h"])
def test_o2_anything_else_uses_the_in_memory_store(url: str) -> None:
    assert store_url_for(url) is None


# ─── O3 authorize ────────────────────────────────────────────────────────────


async def test_o3_only_a_bastion_principal_is_authorized() -> None:
    bastion = Principal(iss="bastion", sub="v_abc1234567", role="viewer", aud="paper-trail")
    frontend = Principal(
        iss="paper-trail-frontend", sub="paper-trail-frontend", role="frontend", aud="paper-trail"
    )
    assert await authorize_bastion(_request(bastion)) is True
    assert await authorize_bastion(_request(frontend)) is False
    assert await authorize_bastion(_request(None)) is False


# ─── O4 composed, lifespan running ───────────────────────────────────────────


@pytest.fixture
async def running() -> AsyncIterator[AsyncClient]:
    app: FastAPI = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def test_o4_the_routes_need_a_bastion_token(
    running: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = Keys(monkeypatch)
    url = "/_slowquery/queries"
    missing = await running.get(url)
    frontend = await running.get(url, headers={"X-Platform-Token": keys.frontend_token()})
    bastion = await running.get(url, headers={"X-Platform-Token": keys.bastion_token()})
    assert (missing.status_code, missing.json()) == (401, {"detail": "missing_platform_token"})
    assert frontend.status_code == 403
    assert bastion.status_code == 200
    assert isinstance(bastion.json(), list)
