# coding: utf-8

"""
    Application Settings

    Single source of truth for all runtime configuration.
    Values are read from environment variables or a ``.env`` file at startup.

    Usage
    -----
    from app.config.settings import settings

    settings.sm_base_url   # → "http://localhost:8080"
    settings.sm_timeout    # → 30.0

    Override at runtime:
        SM_BASE_URL=http://192.168.1.10:8080 uvicorn app.main:app

    Copy ``.env.example`` to ``.env`` and edit for local development.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",           # silently ignore unknown env vars
    )

    # ── Slice Manager connection ──────────────────────────────────────

    sm_base_url: str = Field(
        default="http://localhost:8080",
        description="Slice Manager HTTP base URL (control-api)",
    )
    sm_timeout: float = Field(
        default=30.0,
        description="Timeout in seconds for SM API calls (Selenium takes ~30s)",
    )
    sm_health_timeout: float = Field(
        default=5.0,
        description="Timeout in seconds for the /health SM reachability check",
    )

    # ── Logging ───────────────────────────────────────────────────────

    log_level: str = Field(
        default="INFO",
        description="Log level: DEBUG | INFO | WARNING | ERROR",
    )
    log_json: bool = Field(
        default=True,
        description=(
            "Emit structured JSON logs. Set to false for human-readable "
            "output during local development."
        ),
    )

    # ── Circuit Breaker ───────────────────────────────────────────────

    cb_failure_threshold: int = Field(
        default=5,
        description=(
            "Consecutive failures before the circuit opens and starts "
            "rejecting calls immediately."
        ),
    )
    cb_recovery_timeout: float = Field(
        default=30.0,
        description="Seconds the circuit stays OPEN before allowing a probe call.",
    )

    # ── Retry ─────────────────────────────────────────────────────────

    retry_max_attempts: int = Field(
        default=2,
        description=(
            "Total SM call attempts including the first try. "
            "Set to 1 to disable retries."
        ),
    )
    retry_min_wait: float = Field(
        default=0.5,
        description="Base wait time in seconds between retry attempts.",
    )
    retry_max_wait: float = Field(
        default=3.0,
        description="Maximum wait time in seconds between retry attempts.",
    )


# Module-level singleton — import this everywhere instead of os.getenv()
settings = Settings()
