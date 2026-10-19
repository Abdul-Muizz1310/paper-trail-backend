"""Middleware — X-Request-Id propagation and CORS."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from paper_trail.core.config import settings

_Handler = Callable[[Request], Awaitable[Response]]

ALLOWED_REQUEST_HEADERS = (
    "content-type",
    "x-platform-token",
    "x-request-id",
    "authorization",
    "last-event-id",
)


def install_middleware(app: FastAPI) -> None:
    """Attach CORS and request-id middleware to ``app``."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        # Spec 10 §6: the token and request-id headers must survive a preflight.
        allow_headers=list(ALLOWED_REQUEST_HEADERS),
    )

    @app.middleware("http")
    async def _request_id_middleware(request: Request, call_next: _Handler) -> Response:
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        return response
