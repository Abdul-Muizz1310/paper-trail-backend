"""Ops-wiring invariants — CI must actually execute every tier the repo ships.

`tests/integration/test_pg_concurrent_sessions.py` exists specifically to catch
the SSE-commit bug that escaped behind a 99% coverage badge, but for a while no
CI job ever selected the ``integration`` marker, so that guard gated nothing.
That is the "built, tested, never wired" failure mode. These tests turn the
wiring itself into an asserted invariant instead of a convention: if someone
drops the integration job (or the smoke job, or Dependabot) the suite goes red.

The same applies in the other direction: the smoke tier talks to a *live*
deployment, so it must be excluded from the fast tier and must skip cleanly when
its base-URL env var is unset. Otherwise a suspended host turns CI red.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DEPENDABOT_PATH = REPO_ROOT / ".github" / "dependabot.yml"

SMOKE_URL_ENV = "PAPER_TRAIL_BASE_URL"


def _load_yaml(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"missing required ops file: {path}"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path} must parse to a mapping"
    return data


def _jobs() -> dict[str, dict[str, Any]]:
    jobs = _load_yaml(CI_PATH).get("jobs")
    assert isinstance(jobs, dict) and jobs, "ci.yml declares no jobs"
    return jobs


def _run_commands(job: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for step in job.get("steps") or []:
        if isinstance(step, dict) and isinstance(step.get("run"), str):
            out.append(step["run"])
    return out


def _pytest_commands(job: dict[str, Any]) -> list[str]:
    return [c for c in _run_commands(job) if "pytest" in c]


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in (job.get("steps") or []) if isinstance(s, dict)]


# --------------------------------------------------------------------------
# fast tier
# --------------------------------------------------------------------------


def test_ci_fast_tier_deselects_slow_integration_and_smoke() -> None:
    """The default job must not try to run the docker/live tiers."""
    fast = [c for job in _jobs().values() for c in _pytest_commands(job) if "not slow" in c]
    assert fast, "no CI job runs the fast pytest tier"
    for cmd in fast:
        assert "not integration" in cmd
        assert "not smoke" in cmd


# --------------------------------------------------------------------------
# integration tier (Testcontainers Postgres)
# --------------------------------------------------------------------------


def test_ci_runs_the_integration_marker() -> None:
    """A CI job must select `-m integration`, or the Postgres tier gates nothing."""
    matching = [
        (name, cmd)
        for name, job in _jobs().items()
        for cmd in _pytest_commands(job)
        if '-m "integration"' in cmd or "-m integration" in cmd
    ]
    assert matching, "no CI job runs `pytest -m integration` — the Postgres tier is dead weight"


def test_ci_integration_job_disables_the_coverage_gate() -> None:
    """The integration tier is a handful of tests; --cov-fail-under=80 would fail it.

    pyproject `addopts` injects the 80% gate into every pytest run, so the
    single-tier jobs have to opt out explicitly or the job fails for a bogus
    reason (low coverage) rather than a real one.
    """
    cmds = [
        cmd
        for job in _jobs().values()
        for cmd in _pytest_commands(job)
        if '-m "integration"' in cmd or "-m integration" in cmd
    ]
    assert cmds
    for cmd in cmds:
        assert "--no-cov" in cmd, f"integration pytest run needs --no-cov: {cmd!r}"


# --------------------------------------------------------------------------
# smoke tier (live deployment)
# --------------------------------------------------------------------------


def test_ci_has_env_gated_post_deploy_smoke_job() -> None:
    """A smoke job must exist and take its target URL from repo config, not a literal.

    Env-gated on purpose: the free-tier host can be suspended, so an
    unconditional live check would make CI red for reasons unrelated to the
    commit. `tests/smoke/` skips when the var is empty.
    """
    smoke_steps = [
        step
        for job in _jobs().values()
        for step in _steps(job)
        if isinstance(step.get("run"), str)
        and "pytest" in step["run"]
        and "-m smoke" in step["run"].replace('-m "smoke"', "-m smoke")
    ]
    assert smoke_steps, "no CI step runs `pytest -m smoke`"
    for step in smoke_steps:
        env = step.get("env") or {}
        assert SMOKE_URL_ENV in env, f"smoke step must pass {SMOKE_URL_ENV}"
        wired = str(env[SMOKE_URL_ENV])
        assert "secrets." in wired or "vars." in wired, (
            "smoke target must come from secrets/vars so it can be left unset"
        )
        assert "--no-cov" in step["run"], "smoke pytest run needs --no-cov"


def test_smoke_tier_skips_when_base_url_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate itself: with no base URL configured the smoke tier must skip."""
    from tests.smoke import live_target

    monkeypatch.delenv(SMOKE_URL_ENV, raising=False)
    assert live_target.base_url() is None

    monkeypatch.setenv(SMOKE_URL_ENV, "   ")
    assert live_target.base_url() is None

    monkeypatch.setenv(SMOKE_URL_ENV, " https://example.invalid/ ")
    assert live_target.base_url() == "https://example.invalid"


# --------------------------------------------------------------------------
# Dependabot
# --------------------------------------------------------------------------


def test_dependabot_covers_actions_and_uv_weekly() -> None:
    cfg = _load_yaml(DEPENDABOT_PATH)
    assert cfg.get("version") == 2
    updates = cfg.get("updates")
    assert isinstance(updates, list) and updates
    by_ecosystem = {u.get("package-ecosystem"): u for u in updates if isinstance(u, dict)}
    assert {"github-actions", "uv"} <= set(by_ecosystem), (
        f"dependabot must cover github-actions + uv, got {sorted(by_ecosystem)}"
    )
    for name, entry in by_ecosystem.items():
        schedule = entry.get("schedule")
        assert isinstance(schedule, dict), f"{name} needs a schedule"
        assert schedule.get("interval") == "weekly", f"{name} must be weekly"
        assert entry.get("directory") == "/", f"{name} must target the repo root"
