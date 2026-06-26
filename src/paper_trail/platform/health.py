"""Platform endpoints — /health and /version."""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

import paper_trail
from paper_trail.core.db import session_scope

SERVICE_NAME = "paper_trail"


def _version() -> str:
    """Resolve the package version, falling back to ``__version__``."""
    try:
        return _pkg_version("paper_trail")
    except PackageNotFoundError:
        return paper_trail.__version__


def _commit_sha() -> str:
    """Resolve the deployed commit SHA from the environment."""
    return os.environ.get("GIT_SHA") or os.environ.get("RENDER_GIT_COMMIT", "unknown")


async def _db_status() -> str:
    """Probe the database with ``SELECT 1``; return ``"ok"`` or ``"down"``."""
    try:
        async with session_scope() as s:
            await s.execute(text("SELECT 1"))
    except Exception:
        return "down"
    return "ok"


def install_health_routes(app: FastAPI) -> None:
    """Attach /health and /version endpoints to ``app``."""

    @app.get("/health", include_in_schema=False)
    async def _health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": SERVICE_NAME,
                "version": _version(),
                "commit_sha": _commit_sha(),
                "db": await _db_status(),
            }
        )

    @app.get("/version", include_in_schema=False)
    async def _version_route() -> JSONResponse:
        return JSONResponse(
            {
                "service": SERVICE_NAME,
                "version": _version(),
                "commit_sha": _commit_sha(),
            }
        )
