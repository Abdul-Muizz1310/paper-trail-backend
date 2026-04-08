"""FastAPI entry point for paper_trail."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from paper_trail.api.routers.debates import router as debates_router
from paper_trail.core.config import settings
from paper_trail.core.platform import install_platform_middleware

app = FastAPI(title="paper_trail", version="0.1.0")
install_platform_middleware(app, service_name="paper_trail")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(debates_router)
