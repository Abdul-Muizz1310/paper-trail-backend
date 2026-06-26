"""Prometheus metrics — /metrics exposition endpoint."""

from __future__ import annotations

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator


def install_metrics(app: FastAPI) -> None:
    """Instrument ``app`` and expose Prometheus metrics at /metrics."""
    Instrumentator().instrument(app).expose(app, include_in_schema=False)
