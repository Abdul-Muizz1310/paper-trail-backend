"""Unit tests for core/platform_auth.py."""

from __future__ import annotations

from paper_trail.core import platform_auth
from paper_trail.core.config import settings


def test_demo_mode_accepts_any(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "demo_mode", True)
    assert platform_auth.verify_platform_token(None) is True
    assert platform_auth.verify_platform_token("anything") is True


def test_non_demo_mode_rejects_stub(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "demo_mode", False)
    assert platform_auth.verify_platform_token(None) is False
    assert platform_auth.verify_platform_token("garbage") is False
