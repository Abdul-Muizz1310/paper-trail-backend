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
    # Evidence grounding: how many top search hits to fetch full article text
    # for (0 disables the fetch step — snippet-only, legacy behavior).
    evidence_fetch_top_n: int = 3
    evidence_fetch_char_limit: int = 4000

    # Langfuse
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = ""

    # Public base URL — used to build absolute transcript URLs (spec 99).
    public_base_url: str = "http://localhost:8000"

    # Upstash Redis-backed rate limiting for the paid debate endpoints.
    # Disabled by default so local/dev/CI never touch the network; enable in
    # production (render.yaml) where the Upstash REST creds are provisioned.
    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = ""
    rate_limit_enabled: bool = False
    rate_limit_max_requests: int = 30
    rate_limit_window_s: int = 60

    # Prometheus /metrics auth. Empty = publicly exposed (dev default); set a
    # shared bearer secret in production to gate scraping (SEC-1).
    metrics_token: str = ""

    # Ed25519 private key (PEM) used to sign transcript receipts. Empty = no
    # signature is produced (receipts carry only the content hash).
    transcript_signing_key: str = ""

    # Async SQLAlchemy connection-pool tuning (Postgres/Neon only; ignored for
    # sqlite). pool_pre_ping/pool_recycle guard against Neon dropping idle
    # connections; the size/overflow/timeout cap concurrency (REL-1/REL-2).
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: float = 30.0

    # Overall wall-clock deadline for a single debate run (REL-3). Bounds the
    # worst-case time one debate can pin a background worker under upstream
    # rate-limit storms.
    debate_deadline_s: float = 300.0

    @property
    def cors_origins_list(self) -> list[str]:
        if not self.cors_origins:
            return []
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
