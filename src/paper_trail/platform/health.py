"""Platform endpoints — /health and /version."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

SERVICE_NAME = "paper_trail"


def install_health_routes(app: FastAPI) -> None:
    """Attach /health and /version endpoints to ``app``."""

    @app.get("/health", include_in_schema=False)
    async def _health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": SERVICE_NAME,
                "version": "0.1.0",
                "db": "unknown",
            }
        )

    @app.get("/version", include_in_schema=False)
    async def _version() -> JSONResponse:
        return JSONResponse({"service": SERVICE_NAME, "version": "0.1.0"})
