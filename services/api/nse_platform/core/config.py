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
    # Per process, and shared by request handlers, the scheduler thread and the bootstrap thread. Managed
    # Postgres plans cap *total* connections low (Render's smallest allows roughly 22 across all clients and
    # all replicas), so these defaults stay small on purpose: an over-sized pool is what exhausts the server
    # and gets connections terminated underneath us.
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_pool_timeout: int = 10
    db_pool_recycle: int = 300  # managed hosts drop idle connections well inside 30 minutes
    db_connect_timeout: int = 10  # so a DNS or routing failure cannot block a thread indefinitely

    # Bounded exponential retry for transient database connectivity failures (DNS, TLS teardown, dropped
    # connections). Query, data and integrity errors are never retried.
    db_retry_attempts: int = 4
    db_retry_base_seconds: float = 0.5
    db_retry_max_seconds: float = 15.0
    # How long start-up waits for the database before giving up on the background threads (the API itself
    # still binds its port and serves /health/live regardless).
    db_ready_timeout_seconds: float = 300.0
    # How often a background job re-probes connectivity while the database is down. Keeps a 5-second poll
    # from re-opening a connection - and logging a traceback - on every tick of an outage.
    db_gate_recheck_seconds: float = 30.0

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
    # Leave the URL empty to disable; RENDER_EXTERNAL_URL and RAILWAY_PUBLIC_DOMAIN are picked up
    # automatically. Railway does not sleep instances by default, so it is normally not needed there.
    self_ping_url: str | None = None
    self_ping_minutes: int = 10
    market_timezone: str = "Africa/Nairobi"
    quote_poll_minutes: int = 15
    backtest_poll_seconds: int = 5
    # First-run data load, on a background thread just after start-up. On by default and needing no other
    # setting: it does nothing unless a provider key is set and the database holds no real price bars yet.
    bootstrap_on_start: bool = True
    bootstrap_backfill_years: int = 3
    # The first-run load is the only thing that fills a fresh database, and it starts at the moment a
    # managed database is least likely to answer. Retry it rather than leave the deployment empty.
    bootstrap_max_attempts: int = 5
    bootstrap_retry_max_seconds: float = 300.0
    # How often the catch-up job re-checks for missing data. It is one cheap query once the universe is
    # complete, and the only thing that finishes a load on a database that restarts mid-pass.
    catchup_poll_minutes: int = 15

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
