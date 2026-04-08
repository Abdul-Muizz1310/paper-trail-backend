"""Article fetch + trafilatura extraction."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FetchedDoc:
    url: str
    text: str


async def fetch(url: str) -> FetchedDoc:
    raise NotImplementedError
