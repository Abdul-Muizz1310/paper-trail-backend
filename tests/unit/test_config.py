"""Tests for core.config Settings."""

from __future__ import annotations

from paper_trail.core.config import Settings


def test_cors_origins_list_empty() -> None:
    s = Settings(cors_origins="")
    assert s.cors_origins_list == []


def test_cors_origins_list_parses_and_strips() -> None:
    s = Settings(cors_origins="a,b, c")
    assert s.cors_origins_list == ["a", "b", "c"]


def test_demo_mode_defaults_false() -> None:
    # Hermetic: ignore any developer-local .env (which may set DEMO_MODE=true)
    # so this asserts the *code* default. Fail-closed means the default is
    # False; a real deploy must opt in explicitly (see render.yaml / finding #2).
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.demo_mode is False
