"""Resolve the live deployment the smoke tier should probe.

Deliberately *opt-in*: the smoke tier hits a real host, and this service runs on
a free plan that can be suspended or cold-started. If the tier ran
unconditionally, an unrelated hosting hiccup would turn CI red for every commit.
So the target comes from an env var and the tier skips when it is absent or
blank (GitHub Actions substitutes an unset secret as the empty string, which is
exactly the blank case).
"""

from __future__ import annotations

import os

BASE_URL_ENV = "PAPER_TRAIL_BASE_URL"


def base_url() -> str | None:
    """Return the configured base URL without its trailing slash, else ``None``.

    ``None`` means "not configured" — callers must skip rather than guess a
    default, so a typo can never silently probe the wrong host.
    """
    raw = os.environ.get(BASE_URL_ENV)
    if raw is None:
        return None
    cleaned = raw.strip().rstrip("/")
    return cleaned or None
