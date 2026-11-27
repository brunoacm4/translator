# coding: utf-8

"""
    Application Settings

    Single source of truth for all runtime configuration.
    Values are read from environment variables or a ``.env`` file at startup.

    Usage
    -----
    from app.config.settings import settings

    settings.sm_base_url   # → "http://localhost:8080"
    settings.sm_timeout    # → 15.0

    Override at runtime:
        SM_BASE_URL=http://192.168.1.10:8080 hypercorn app.main:app

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
        default=15.0,
        description=(
            "Timeout in seconds for SM API calls. With one attempt it keeps the "
            "answer to the NEF under the NEF's 25 s limit, and it outlasts the "
            "SM's own 10 s wait for Kafka (CONSIDERATIONS.md §4.6)."
        ),
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
        default=1,
        description=(
            "Total SM call attempts including the first try. "
            "1 disables retries (the default): a timed-out POST may already "
            "have reached the SM, and a retry would not fit the NEF's 25 s limit."
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

    # ── SM operation polling ──────────────────────────────────────────

    sm_polling_enabled: bool = Field(
        default=True,
        description=(
            "Master switch for async SM operation polling. When true, the "
            "translator polls GET /operations/{request_id} for every SM write "
            "until it is completed or failed, then fires the NEF completion "
            "callback. The SM implements that endpoint since Sept 2026; set to "
            "false against an older SM (it returns 500 there), in which case "
            "the synchronous 202 is treated as the terminal result."
        ),
    )
    sm_poll_initial_interval: float = Field(
        default=2.0,
        description=(
            "Initial polling interval in seconds when waiting for an async SM "
            "operation to complete. Doubles on each attempt up to sm_poll_max_interval."
        ),
    )
    sm_poll_max_interval: float = Field(
        default=30.0,
        description="Maximum polling interval cap in seconds (exponential backoff ceiling).",
    )
    sm_poll_timeout: float = Field(
        default=300.0,
        description=(
            "Total time in seconds before a polling task gives up and marks "
            "the operation as failed. Default 5 minutes."
        ),
    )

    # ── Translator state persistence ─────────────────────────────────

    translator_db_path: str = Field(
        default="./translator.db",
        description="Path to the SQLite DB used for subscriptions and idempotency state.",
    )


# Module-level singleton — import this everywhere instead of os.getenv()
settings = Settings()
