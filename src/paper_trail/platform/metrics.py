"""Prometheus metrics — /metrics exposition endpoint.

When `METRICS_TOKEN` is configured the endpoint requires a matching
`Authorization: Bearer <token>` (SEC-1: don't leak per-route traffic/latency
intelligence unauthenticated on the public URL). With no token set the metrics
are exposed publicly, which is the convenient default for local/dev.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from prometheus_fastapi_instrumentator import Instrumentator

from paper_trail.core.config import settings


def install_metrics(app: FastAPI) -> None:
    """Instrument ``app`` and expose Prometheus metrics at /metrics."""
    instrumentator = Instrumentator().instrument(app)
    token = settings.metrics_token
    if not token:
        instrumentator.expose(app, include_in_schema=False)
        return

    @app.get("/metrics", include_in_schema=False)
    async def _metrics(authorization: Annotated[str | None, Header()] = None) -> Response:
        expected = f"Bearer {token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="unauthorized")
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
