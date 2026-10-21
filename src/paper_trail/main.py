"""FastAPI entry point for paper_trail."""

from __future__ import annotations

from fastapi import FastAPI

from paper_trail.api.routers.debates import router as debates_router
from paper_trail.api.routers.platform import router as platform_router
from paper_trail.core.config import settings
from paper_trail.core.db import make_engine
from paper_trail.platform.health import install_health_routes
from paper_trail.platform.logging import configure_logging
from paper_trail.platform.metrics import install_metrics
from paper_trail.platform.middleware import install_middleware
from paper_trail.platform.platform_token import install_platform_token
from paper_trail.platform.query_observability import install_query_observability

# Activate structured JSON logging (+ request_id/service fields) before any
# module logs, so production observability actually engages.
configure_logging()


def create_app() -> FastAPI:
    """Assemble the app. Starlette runs the middleware added last outermost, so
    the token check goes in first: CORS and request ids wrap it, a preflight
    never reaches it, and its 401/503 answers still carry CORS headers
    (docs/specs/10-platform-contract-v2.md §6). slowquery-detective goes last
    of all, so its request-id tagger wraps everything (spec 11)."""
    app = FastAPI(title="paper_trail", version="0.1.0")
    install_platform_token(app)
    install_health_routes(app)
    install_metrics(app)
    install_middleware(app)
    app.include_router(debates_router)
    app.include_router(platform_router)
    install_query_observability(app, make_engine(), settings.database_url)
    return app


app = create_app()
