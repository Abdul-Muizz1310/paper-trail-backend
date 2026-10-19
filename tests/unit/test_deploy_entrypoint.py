"""The container entrypoint applies Alembic migrations (spec 06, deploy-time migrations).

Render's free plan never runs a ``preDeployCommand``, so the migration step has
to live in the container. These tests execute ``docker-entrypoint.sh`` with a
stub ``alembic`` on ``PATH``: they assert the branch behaviour, not the text.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "docker-entrypoint.sh"
_SH = shutil.which("sh")
requires_sh = pytest.mark.skipif(_SH is None, reason="no POSIX shell")


def _run(
    tmp_path: Path, database_url: str | None, *, alembic_exit: int = 0
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the entrypoint with a stub ``alembic`` that logs its argv and exits ``alembic_exit``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "alembic-calls.log"
    stub = bin_dir / "alembic"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit {alembic_exit}\n', encoding="utf-8")
    stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    if database_url is not None:
        env["DATABASE_URL"] = database_url
    assert _SH is not None
    result = subprocess.run(
        [_SH, str(ENTRYPOINT), "printf", "%s|%s", "SERVER_STARTED", "two words"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, calls


@requires_sh
@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://u:p@host/db?ssl=require",
        "postgres://u:p@host/db",
        "postgresql://u:p@host/db",
    ],
)
def test_m1_a_postgres_url_runs_alembic_upgrade_head_then_the_cmd(tmp_path: Path, url: str) -> None:
    result, calls = _run(tmp_path, url)
    assert result.returncode == 0, result.stderr
    assert calls == ["upgrade head"]
    assert result.stdout.endswith("SERVER_STARTED|two words")


@requires_sh
@pytest.mark.parametrize("url", [None, "", "sqlite+aiosqlite:///:memory:"])
def test_m2_no_postgres_url_skips_alembic_and_still_starts(tmp_path: Path, url: str | None) -> None:
    result, calls = _run(tmp_path, url)
    assert result.returncode == 0, result.stderr
    assert calls == []
    assert result.stdout.endswith("SERVER_STARTED|two words")


@requires_sh
def test_m3_a_failed_migration_aborts_the_boot(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, "postgresql+asyncpg://u:p@host/db", alembic_exit=3)
    assert result.returncode != 0
    assert calls == ["upgrade head"]
    assert "SERVER_STARTED" not in result.stdout


@requires_sh
def test_m4_the_cmd_arguments_reach_it_unchanged(tmp_path: Path) -> None:
    result, _ = _run(tmp_path, None)
    # printf got its format and both arguments, "two words" still one argument.
    assert "SERVER_STARTED|two words" in result.stdout


def test_m5_the_dockerfile_wires_the_entrypoint_and_render_has_no_pre_deploy() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY docker-entrypoint.sh" in dockerfile
    assert 'ENTRYPOINT ["./docker-entrypoint.sh"]' in dockerfile
    cmd = re.search(r"^CMD (\[.*?\])", dockerfile, re.MULTILINE | re.DOTALL)
    assert cmd is not None, "CMD must be exec form so the entrypoint can exec it"
    argv = json.loads(cmd.group(1).replace("\\\n", " "))
    assert argv[:2] == ["uvicorn", "paper_trail.main:app"]

    service = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))["services"][0]
    assert service["plan"] == "free"
    assert "preDeployCommand" not in service

    assert os.access(ENTRYPOINT, os.X_OK), "the entrypoint must be executable in git"
