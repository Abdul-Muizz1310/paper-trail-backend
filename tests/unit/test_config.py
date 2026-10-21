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


# ─── Model slot defaults (docs/specs/04-llm-router.md "Default slugs") ────────

_FREE_DEFAULTS = {
    "openrouter_model_primary": "google/gemma-4-26b-a4b-it:free",
    "openrouter_model_fast": "minimax/minimax-m3.1-flash:free",
    "openrouter_model_fallback": "minimax/minimax-m3.1-flash:free",
}


def _settings_without_env(monkeypatch) -> Settings:  # type: ignore[no-untyped-def]
    for var in ("OPENROUTER_MODEL_PRIMARY", "OPENROUTER_MODEL_FAST", "OPENROUTER_MODEL_FALLBACK"):
        monkeypatch.delenv(var, raising=False)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_d1_model_slots_default_to_the_free_picks(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    s = _settings_without_env(monkeypatch)
    assert {field: getattr(s, field) for field in _FREE_DEFAULTS} == _FREE_DEFAULTS


def test_d2_every_default_model_is_a_free_slug(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    s = _settings_without_env(monkeypatch)
    for field in _FREE_DEFAULTS:
        assert getattr(s, field).endswith(":free"), field


def test_d3_env_overrides_each_model_slot(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPENROUTER_MODEL_PRIMARY", "vendor/primary:free")
    monkeypatch.setenv("OPENROUTER_MODEL_FAST", "vendor/fast:free")
    monkeypatch.setenv("OPENROUTER_MODEL_FALLBACK", "vendor/fallback:free")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (s.openrouter_model_primary, s.openrouter_model_fast, s.openrouter_model_fallback) == (
        "vendor/primary:free",
        "vendor/fast:free",
        "vendor/fallback:free",
    )


def test_the_llm_node_deadline_defaults_to_120_s_and_must_be_positive() -> None:
    import pydantic

    assert Settings(_env_file=None).llm_node_deadline_s == 120.0  # type: ignore[call-arg]
    for bad in (0, -1):
        try:
            Settings(_env_file=None, llm_node_deadline_s=bad)  # type: ignore[call-arg]
        except pydantic.ValidationError:
            continue
        raise AssertionError(f"llm_node_deadline_s={bad} was accepted")
