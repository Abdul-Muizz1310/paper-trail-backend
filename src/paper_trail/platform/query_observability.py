"""Query observability: slowquery-detective 0.2 behind contract v2 (docs/specs/11-query-observability.md).

``install_query_observability`` must be the last middleware call at app
construction: the library's request-id tagger then wraps every other layer, so
each statement a request causes, its background tasks included, is counted
against that request's ``X-Request-Id``. ``GET /_slowquery/queries?request_id=``
answers bastion only.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from slowquery_detective import install
from sqlalchemy.ext.asyncio import AsyncEngine

from paper_trail.platform.platform_token import principal_of
from paper_trail.platform.token_verifier import BASTION_ISSUER, SERVICE_ID

_POSTGRES_SCHEMES = ("postgres://", "postgresql://", "postgresql+")


def store_url_for(database_url: str) -> str | None:
    """The library's store URL: the service's own Postgres, else ``None`` (in-memory)."""
    return database_url if database_url.startswith(_POSTGRES_SCHEMES) else None


async def authorize_bastion(request: Request) -> bool:
    """Only a request contract v2 authenticated with bastion's key may read the measurements."""
    principal = principal_of(request)
    return principal is not None and principal.iss == BASTION_ISSUER


def install_query_observability(app: FastAPI, engine: AsyncEngine, database_url: str) -> None:
    """Install slowquery-detective on ``app`` for this service's engine."""
    install(
        app,
        engine,
        service=SERVICE_ID,
        store_url=store_url_for(database_url),
        authorize=authorize_bastion,
    )
