# coding: utf-8

"""
    Correlation ID Middleware

    Assigns a unique UUID to every inbound request and propagates it
    through the entire async call chain via a Python ``ContextVar``.

    Why this matters
    ----------------
    When a single NEF request triggers multiple SM calls (create + associate),
    all log lines from all involved modules share the same correlation_id,
    making it trivial to reconstruct what happened in Grafana/Loki:

        {job="translator"} | json | correlation_id="abc-123"

    Header contract
    ---------------
    - If the caller sends  ``X-Correlation-ID: <value>``  that value is used.
    - Otherwise a new UUIDv4 is generated.
    - The correlation ID is always echoed back in the response header so the
      NEF can correlate its own logs with the translator's.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# ── Module-level context variable ─────────────────────────────────────
# ContextVar survives async task switches within the same request but is
# isolated between concurrent requests — exactly what we need.
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")

logger = logging.getLogger(__name__)

HEADER_NAME = "X-Correlation-ID"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that injects a correlation ID into every request.

    Registration in main.py::

        app.add_middleware(CorrelationIdMiddleware)
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        corr_id: str = request.headers.get(HEADER_NAME) or str(uuid.uuid4())
        correlation_id_var.set(corr_id)

        response: Response = await call_next(request)
        response.headers[HEADER_NAME] = corr_id
        return response
