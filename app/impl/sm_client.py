# coding: utf-8

"""
    Slice Manager HTTP Client

    Thin async wrapper around the IT Aveiro Slice Manager REST API.
    Each method accepts a plain ``dict`` payload and fires an HTTP POST
    to the corresponding SM endpoint.

    Resilience features
    -------------------
    Every outbound call is wrapped with:

    1. **Retry with exponential backoff + jitter** — retries once on transient
       network failures (timeout, connection refused) before giving up.
    2. **Circuit breaker** — after ``CB_FAILURE_THRESHOLD`` consecutive failures
       the circuit opens and subsequent calls fail immediately with HTTP 503,
       avoiding 30 s Selenium timeouts piling up during SM maintenance.

    Singleton connection pool
    -------------------------
    The underlying ``httpx.AsyncClient`` is stored as a class-level variable
    so the TCP connection pool is shared across all requests.  Call
    ``SliceManagerClient.close_shared()`` during graceful shutdown.

    The SM returns **empty bodies** (HTTP 200) on success, so all methods
    return ``None``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

from app.config.settings import settings
from app.resilience.circuit_breaker import CircuitBreakerOpen, sm_circuit_breaker
from app.resilience.retry import retry_with_backoff

logger = logging.getLogger(__name__)

# Shared httpx client — one connection pool for the entire process lifetime
_shared_http_client: Optional[httpx.AsyncClient] = None


class SliceManagerClient:
    """Async HTTP client for the IT Aveiro Slice Manager."""

    def __init__(self, base_url: Optional[str] = None) -> None:
        self.base_url = base_url or settings.sm_base_url

    # -- lifecycle -----------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        """Return (or lazily create) the shared httpx connection pool."""
        global _shared_http_client
        if _shared_http_client is None or _shared_http_client.is_closed:
            _shared_http_client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=settings.sm_timeout,
            )
        return _shared_http_client

    @classmethod
    async def close_shared(cls) -> None:
        """Close the shared connection pool.  Call this during graceful shutdown."""
        global _shared_http_client
        if _shared_http_client is not None and not _shared_http_client.is_closed:
            await _shared_http_client.aclose()
            _shared_http_client = None
            logger.info("SM shared HTTP client closed")

    # -- internal helper -----------------------------------------------------

    async def _post(self, path: str, payload: Dict[str, Any]) -> None:
        """
        Fire a POST request to the SM, wrapped with retry and circuit breaker.

        Call order:  circuit_breaker.call  →  retry_with_backoff  →  httpx.post

        This means:
        - If the circuit is OPEN: fail immediately with CircuitBreakerOpen (→ 503)
        - If a transient error occurs: retry up to RETRY_MAX_ATTEMPTS times
        - If retries are exhausted or SM returns 4xx/5xx: raise the exception

        Raises:
            CircuitBreakerOpen    – circuit is OPEN, SM appears to be down
            httpx.TimeoutException
            httpx.ConnectError
            httpx.HTTPStatusError
        """
        logger.info("SM POST %s  payload_keys=%s", path, list(payload.keys()))

        async def _attempt() -> None:
            client = await self._get_client()
            try:
                resp = await client.post(path, json=payload)
                resp.raise_for_status()
                logger.info("SM POST %s  → %s", path, resp.status_code)
            except httpx.TimeoutException:
                logger.error(
                    "SM POST %s  → TIMEOUT (%.0fs)", path, settings.sm_timeout
                )
                raise
            except httpx.ConnectError as exc:
                logger.error("SM POST %s  → CONNECTION REFUSED (%s)", path, exc)
                raise
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "SM POST %s  → HTTP %s  body=%s",
                    path,
                    exc.response.status_code,
                    exc.response.text[:200],
                )
                raise

        try:
            await sm_circuit_breaker.call(
                retry_with_backoff(_attempt, operation_name=f"SM POST {path}")
            )
        except CircuitBreakerOpen as exc:
            # Re-raise so translator_service maps it to 503
            raise

    # -- SM endpoints --------------------------------------------------------

    async def create_slice(self, payload: Dict[str, Any]) -> None:
        """
        POST /core/slice/create

        Creates a new network slice in the Slice Manager core.
        The SM returns an empty body — the translator generates the
        ``slice_id`` beforehand and passes it inside the payload.

        Args:
            payload: Dict matching CoreSliceCreatePostRequest fields.
        """
        await self._post("/core/slice/create", payload)

    async def associate_slice(self, payload: Dict[str, Any]) -> None:
        """
        POST /core/slice/associate

        Associates a UE (IMSI) with an existing slice.

        Args:
            payload: Dict matching CoreSliceAssociatePostRequest fields.
        """
        await self._post("/core/slice/associate", payload)

    async def change_slice(self, payload: Dict[str, Any]) -> None:
        """
        POST /core/slice/change

        Modifies QoS / throughput parameters of an existing slice association.

        Args:
            payload: Dict matching CoreSliceChangePostRequest fields.
        """
        await self._post("/core/slice/change", payload)

    async def delete_slice(self, payload: Dict[str, Any]) -> None:
        """
        POST /core/slice/delete

        Deletes (terminates) a slice in the Slice Manager core.

        Args:
            payload: Dict matching CoreSliceDeletePostRequest fields.
        """
        await self._post("/core/slice/delete", payload)
