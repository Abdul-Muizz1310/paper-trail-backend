"""Article fetch + trafilatura extraction."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import trafilatura

from paper_trail.core.errors import ToolError


@dataclass(frozen=True)
class FetchedDoc:
    url: str
    text: str


async def fetch(url: str) -> FetchedDoc:
    """Fetch a URL and extract main-content text via trafilatura."""
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
    except httpx.HTTPError as e:
        raise ToolError("fetch_http_error", str(e)) from e
    if resp.status_code != 200:
        raise ToolError("fetch_bad_status", f"status={resp.status_code}")
    html = resp.text
    if not html.strip():
        raise ToolError("fetch_empty_body", url)
    extracted = trafilatura.extract(html)
    if not extracted or not extracted.strip():
        raise ToolError("fetch_empty_extraction", url)
    return FetchedDoc(url=url, text=extracted.strip())
