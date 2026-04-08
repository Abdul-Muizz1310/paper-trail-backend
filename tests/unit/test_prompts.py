"""Unit tests for core/prompts.py."""

from __future__ import annotations

import pytest

from paper_trail.core import prompts


def test_load_plan_prompt() -> None:
    out = prompts.load("plan")
    assert isinstance(out, str)
    assert len(out) > 0
    # Frontmatter should be stripped if present.
    assert not out.startswith("---")


def test_load_all_known_prompts() -> None:
    for name in ("plan", "proponent", "skeptic", "judge", "render"):
        body = prompts.load(name)
        assert body.strip()


def test_load_missing_prompt_raises() -> None:
    with pytest.raises(FileNotFoundError):
        prompts.load("does_not_exist_xyz")
