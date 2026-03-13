# coding: utf-8

"""
    Structured Logging Configuration

    The translator runs inside the same infrastructure as the Slice Manager,
    which uses the Loki → Promtail → Grafana stack for log aggregation.

    To be ingested by that pipeline without extra Promtail parsing rules,
    every log line must be a valid JSON object with at least:
        - ``timestamp``      ISO-8601 UTC
        - ``level``          DEBUG / INFO / WARNING / ERROR
        - ``message``        human-readable string
        - ``correlation_id`` (when set — ties all lines from one request together)

    Development mode
    ----------------
    Set ``LOG_JSON=false`` in your ``.env`` for readable human output::

        10:45:01 [INFO    ] app.impl.translator_service — Creating subscription …

    Production mode (default)
    -------------------------
    Each line is compact JSON::

        {"timestamp":"2026-03-12T…","level":"INFO","logger":"app.impl.translator_service","message":"Creating subscription …","correlation_id":"abc-123"}
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from app.middleware.correlation_id import correlation_id_var


class JsonFormatter(logging.Formatter):
    """
    Converts a ``LogRecord`` to a single-line JSON string.

    Automatically picks up the ``correlation_id`` from the async context
    so every log line from a request is tagged — no manual threading needed.
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Inject correlation ID from the per-request ContextVar
        corr_id = correlation_id_var.get("")
        if corr_id:
            entry["correlation_id"] = corr_id

        # Append traceback if present
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(entry, default=str)


def configure_logging(log_level: str = "INFO", json_logs: bool = True) -> None:
    """
    Configure the root logger for the translator process.

    Call this once at application startup (inside the FastAPI lifespan).

    Args:
        log_level: Minimum log level as a string (e.g. ``"DEBUG"``).
        json_logs: If ``True`` emit JSON; if ``False`` emit plain text.
    """
    handler = logging.StreamHandler()

    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.root.setLevel(level)
    logging.root.handlers = [handler]

    # Silence overly verbose third-party libraries
    for lib in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(lib).setLevel(logging.WARNING)
