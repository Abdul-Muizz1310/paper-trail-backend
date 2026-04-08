"""Environment-driven configuration."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "info"
    port: int = 8000
    demo_mode: bool = True
    cors_origins: str = ""

    # Database
    database_url: str = "sqlite+aiosqlite:///:memory:"

    # OpenRouter
    openrouter_api_key: str = "test-key"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model_primary: str = "openai/gpt-4o-mini"
    openrouter_model_fast: str = "openai/gpt-4o-mini"
    openrouter_model_fallback: str = "anthropic/claude-3-haiku"
    openrouter_http_referer: str = "https://github.com/Abdul-Muizz1310"
    openrouter_x_title: str = "muizz-lab-portfolio"
    openrouter_timeout_s: float = 60.0

    # Tavily
    tavily_api_key: str = "test-tavily"

    # Public base URL
    public_base_url: str = "http://localhost:8000"


settings = Settings()
