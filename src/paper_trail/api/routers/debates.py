"""Routes for Debate."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/debates", tags=["debates"])


@router.post("/")
async def debates_create() -> dict[str, str]:
    """POST /debates"""
    return {"handler": "debates.create"}


@router.get("/{id}")
async def debates_get() -> dict[str, str]:
    """GET /debates/{id}"""
    return {"handler": "debates.get"}


@router.get("/")
async def debates_list() -> dict[str, str]:
    """GET /debates"""
    return {"handler": "debates.list"}
