"""FastAPI entry point for paper_trail."""

from __future__ import annotations

from fastapi import FastAPI

from paper_trail.api.routers.debates import router as debates_router
from paper_trail.core.platform import install_platform_middleware

app = FastAPI(title="paper_trail", version="0.1.0")
install_platform_middleware(app, service_name="paper_trail")


app.include_router(debates_router)
