"""Application configuration. All secrets come from the environment (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../../.env"), env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = True

    database_url: str = "postgresql+psycopg://nse:nse@localhost:5432/nse"
    db_pool_size: int = 10
    db_max_overflow: int = 10

    redis_url: str | None = None

    # Auth / security
    session_secret: SecretStr = SecretStr("dev-only-insecure-secret-change-me")
    session_idle_minutes: int = 60 * 12
    session_absolute_hours: int = 24 * 14
    cookie_secure: bool = False
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    allow_registration: bool = True
    rate_limit_per_minute: int = 240
    login_rate_limit_per_minute: int = 10

    # Providers
    mystocks_api_key: SecretStr | None = None
    mystocks_env: Literal["sandbox", "production"] = "sandbox"
    mystocks_timeout_seconds: float = 20.0
    enable_synthetic_provider: bool = True
    allow_synthetic_in_production: bool = False

    # Worker
    # Run the scheduled jobs inside the API process instead of a separate `nse-worker` process. Useful on
    # hosts that do not offer a background worker. The API instance must not sleep, or jobs stop running.
    run_scheduler_in_api: bool = False
    # Keep a free-tier host from sleeping by requesting our own /health/live (no database, no Redis).
    # Leave the URL empty to disable; on Render, RENDER_EXTERNAL_URL is picked up automatically.
    self_ping_url: str | None = None
    self_ping_minutes: int = 10
    market_timezone: str = "Africa/Nairobi"
    quote_poll_minutes: int = 15
    backtest_poll_seconds: int = 5
    # First-run data load, on a background thread just after start-up. On by default and needing no other
    # setting: it does nothing unless a provider key is set and the database holds no real price bars yet.
    bootstrap_on_start: bool = True
    bootstrap_backfill_years: int = 3

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.app_env == "production":
            if self.session_secret.get_secret_value().startswith("dev-only"):
                raise ValueError("SESSION_SECRET must be set in production")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production (HTTPS only)")
            if self.enable_synthetic_provider and not self.allow_synthetic_in_production:
                # Synthetic data must never silently appear in production.
                self.enable_synthetic_provider = False
        return self

    @property
    def mystocks_base_url(self) -> str:
        if self.mystocks_env == "production":
            return "https://mystocks.africa/api/v1/partner"
        return "https://mystocks.africa/api/sandbox/v1/partner"


@lru_cache
def get_settings() -> Settings:
    return Settings()
