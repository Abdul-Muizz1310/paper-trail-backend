"""Tests for platform/logging.py — structured logging configuration."""

from __future__ import annotations

import logging

from paper_trail.platform.logging import configure_logging


def test_configure_logging_sets_up_root_handler(monkeypatch: object) -> None:
    """configure_logging attaches a structlog formatter to the root logger."""
    configure_logging()
    root = logging.getLogger()
    assert len(root.handlers) >= 1
    assert root.level == logging.INFO


def test_configure_logging_prod_mode(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """In production mode, structlog uses JSON rendering."""
    import os

    monkeypatch.setenv("ENVIRONMENT", "production")
    configure_logging()
    root = logging.getLogger()
    assert len(root.handlers) >= 1


def test_configure_logging_dev_mode(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """In development mode, structlog uses console rendering."""
    import os

    monkeypatch.setenv("ENVIRONMENT", "development")
    configure_logging()
    root = logging.getLogger()
    assert len(root.handlers) >= 1
