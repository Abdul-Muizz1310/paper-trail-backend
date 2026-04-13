"""Environment-driven configuration."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "info"
    port: int = 8000
    demo_mode: bool = False
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

    # Langfuse
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = ""

    # Public base URL
    public_base_url: str = "http://localhost:8000"

    @property
    def cors_origins_list(self) -> list[str]:
        if not self.cors_origins:
            return []
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
