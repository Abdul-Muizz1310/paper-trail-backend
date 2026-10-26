"""Environment-driven configuration."""

from __future__ import annotations

import re

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LANGFUSE_ENVIRONMENT = re.compile(r"[a-z0-9_-]{1,40}")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Also the Langfuse environment of every trace (docs/specs/12-tracing.md); see
    # `_langfuse_environment`.
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
    # Free (`:free`) slugs only, each smoke-tested live with JSON mode; the
    # OPENROUTER_MODEL_* env vars override them (docs/specs/04-llm-router.md
    # "Default slugs"). Three upstream providers, so one outage cannot empty the
    # cascade: minimax-m3.1-flash left OpenRouter on 2026-10-26, gemma-4-31b
    # answered 502 on 2026-10-21 and again on 2026-10-26.
    openrouter_model_primary: str = "google/gemma-4-26b-a4b-it:free"
    openrouter_model_fast: str = "xingchen-agi/xing4.0-29b-a4b:free"
    openrouter_model_fallback: str = "dots-studio/dots-3-note-preview:free"
    openrouter_http_referer: str = "https://github.com/Abdul-Muizz1310"
    openrouter_x_title: str = "muizz-lab-portfolio"
    openrouter_timeout_s: float = 60.0
    # Bound on one chat/chat_json call (one node's LLM work) across every tier
    # of the cascade; each tier gets an equal share of what is left (spec 04).
    llm_node_deadline_s: float = Field(default=120.0, gt=0)

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

    @field_validator("app_env")
    @classmethod
    def _langfuse_environment(cls, value: str) -> str:
        """Refuse at startup an APP_ENV Langfuse would drop from every trace."""
        if _LANGFUSE_ENVIRONMENT.fullmatch(value) is None or value.startswith("langfuse"):
            raise ValueError(
                "APP_ENV is also the Langfuse environment: 1-40 of a-z, 0-9, '_' and '-', "
                "not starting with 'langfuse'"
            )
        return value

    @property
    def cors_origins_list(self) -> list[str]:
        if not self.cors_origins:
            return []
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
