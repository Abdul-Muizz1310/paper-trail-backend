"""Session setup shared by every tier.

The suite runs under the test settings (docs/specs/10-platform-contract-v2.md
§3): with no trusted key configured, a protected route passes as anonymous
instead of failing closed with 503. Set before any ``paper_trail`` import, so
the settings object sees it; an exported ``APP_ENV`` or a local ``.env`` cannot
switch the suite into fail-closed mode. Tests that need the production
behaviour set ``settings.app_env`` themselves.
"""

from __future__ import annotations

import os

os.environ["APP_ENV"] = "test"
