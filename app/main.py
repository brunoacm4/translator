# coding: utf-8

"""
    Translator API

    Middleware between a 3GPP NEF and the IT Aveiro Slice Manager.
    Exposes the 3GPP TS 29.122 AsSessionWithQoS northbound API and
    translates requests into Slice Manager operations.

    Base path:  /3gpp-as-session-with-qos/v1
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.apis.translator_api import router as TranslatorApiRouter
from app.config.settings import settings
from app.impl.sm_client import SliceManagerClient
from app.impl.sm_poller import poll_sm_request
from app.logging_config import configure_logging
from app.middleware.correlation_id import CorrelationIdMiddleware
from app.resilience.circuit_breaker import sm_circuit_breaker
from app.store.subscription_store import store
from app.store.repositories import OperationRepository
from app.db.schema import init_db

logger = logging.getLogger(__name__)


# ── Lifespan (startup / graceful shutdown) ─────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Runs once at startup and once at shutdown.

    Startup:
      - Configure logging (JSON or plain text based on LOG_JSON env var)
      - Initialize SQLite tables
      - Log the active configuration so every deployment is traceable

    Shutdown:
      - Close the shared httpx connection pool cleanly so in-flight SM
        requests are not abruptly cut off during a rolling restart
    """
    # Startup
    configure_logging(log_level=settings.log_level, json_logs=settings.log_json)
    init_db()

    # Resume polling for operations that were in flight when the process was last
    # killed. Only relevant while SM async polling is enabled — see
    # settings.sm_polling_enabled (the SM has no /operations endpoint yet).
    if settings.sm_polling_enabled:
        pending_ops = OperationRepository().get_resumable()
        if pending_ops:
            logger.info("Resuming %d in-flight SM polling task(s)", len(pending_ops))
            for op in pending_ops:
                asyncio.create_task(
                    poll_sm_request(
                        sm_request_id=op["sm_request_id"],
                        operation_id=op["operation_id"],
                        notification_url=op.get("notification_url"),
                        subscription_id=op.get("subscription_id"),
                    ),
                    name=f"poll-resume-{op['sm_request_id'][:8]}",
                )

    logger.info(
        "Translator starting  sm_base_url=%s  log_level=%s  json_logs=%s  "
        "cb_threshold=%d  retry_attempts=%d  db_path=%s",
        settings.sm_base_url,
        settings.log_level,
        settings.log_json,
        settings.cb_failure_threshold,
        settings.retry_max_attempts,
        settings.translator_db_path,
    )

    yield  # application runs here

    # Shutdown
    logger.info("Translator shutting down — closing SM HTTP connection pool")
    await SliceManagerClient.close_shared()


# ── Application ────────────────────────────────────────────────────────

app = FastAPI(
    title="3GPP AsSessionWithQoS Translator",
    description=(
        "Middleware that exposes the 3GPP TS 29.122 AsSessionWithQoS API "
        "and translates requests into IT Aveiro Slice Manager operations."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ── Middleware (order matters: outermost registered = innermost executed) ──
app.add_middleware(CorrelationIdMiddleware)  # must be first so all logs are tagged

# ── 3GPP routes ────────────────────────────────────────────────────────
app.include_router(
    TranslatorApiRouter,
    prefix="/3gpp-as-session-with-qos/v1",
)


# ── Health endpoint (outside 3GPP prefix) ──────────────────────────────

@app.get("/health", tags=["ops"])
async def health_check() -> JSONResponse:
    """
    Liveness / readiness probe.

    Returns SM reachability, circuit-breaker state, and subscription count
    so Grafana dashboards can track translator health at a glance.

    HTTP status:
      200 — SM reachable and circuit CLOSED / HALF_OPEN
      503 — SM unreachable or circuit OPEN
    """
    sm_ok = False
    try:
        client = SliceManagerClient()
        http = await client._get_client()
        resp = await http.get("/docs", timeout=settings.sm_health_timeout)
        sm_ok = resp.status_code < 500
    except Exception:
        sm_ok = False

    cb = sm_circuit_breaker.status()
    healthy = sm_ok and cb["state"] != "open"

    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "healthy" if healthy else "degraded",
            "sm_reachable": sm_ok,
            "subscriptions_count": store.count,
            "circuit_breaker": cb,
        },
    )
