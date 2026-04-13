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
    s = Settings()
    assert s.demo_mode is False
