"""Prompt loader for agents/prompts/*.md."""

from __future__ import annotations

from pathlib import Path


def load(name: str) -> str:
    raise NotImplementedError
