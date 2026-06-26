"""FastAPI entry point for paper_trail."""

from __future__ import annotations

from fastapi import FastAPI

from paper_trail.api.routers.debates import router as debates_router
from paper_trail.api.routers.platform import router as platform_router
from paper_trail.core.config import settings
from paper_trail.platform.health import install_health_routes
from paper_trail.platform.metrics import install_metrics
from paper_trail.platform.middleware import install_middleware
from paper_trail.platform.platform_token import install_platform_token

app = FastAPI(title="paper_trail", version="0.1.0")
install_middleware(app)
install_health_routes(app)
install_metrics(app)
install_platform_token(app, demo_mode=settings.demo_mode)

app.include_router(debates_router)
app.include_router(platform_router)
