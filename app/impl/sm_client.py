# coding: utf-8

"""
    Slice Manager HTTP Client

    Thin async wrapper around the IT Aveiro Slice Manager REST API.
    Each method maps to a REST endpoint on the SM control-api.

    Resilience features
    -------------------
    Every outbound call is wrapped with:

    1. **Retry with exponential backoff + jitter** — retries once on transient
       network failures (timeout, connection refused) before giving up.
    2. **Circuit breaker** — after ``CB_FAILURE_THRESHOLD`` consecutive failures
       the circuit opens and subsequent calls fail immediately with HTTP 503,
       avoiding timeouts piling up during SM maintenance.

    Singleton connection pool
    -------------------------
    The underlying ``httpx.AsyncClient`` is stored as a class-level variable
    so the TCP connection pool is shared across all requests.  Call
    ``SliceManagerClient.close_shared()`` during graceful shutdown.

    SM write operations return 202 with ``{request_id, state}``.
    All write methods return the ``request_id`` string so callers can
    launch a polling task for async completion tracking.
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

    async def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        tolerate_not_found: bool = False,
    ) -> str:
        """
        Fire an HTTP request to the SM, wrapped with retry and circuit breaker.

        Args:
            tolerate_not_found: When True, a ``404 Not Found`` response is
                treated as success (empty ``request_id`` returned) instead of
                raising. Used by the DELETE endpoints so the translator's
                delete is idempotent — removing a resource the SM no longer
                knows about is the desired end state, not an error.

        Returns:
            The ``request_id`` UUID string from the SM 202 response body.
            Returns an empty string if the response body is not JSON or the
            ``request_id`` field is absent (should not happen with a spec-compliant SM).

        Raises:
            CircuitBreakerOpen    - circuit is OPEN, SM appears to be down
            httpx.TimeoutException
            httpx.ConnectError
            httpx.HTTPStatusError
        """
        if payload is not None:
            logger.info("SM %s %s  payload_keys=%s", method, path, list(payload.keys()))
        else:
            logger.info("SM %s %s", method, path)

        async def _attempt() -> str:
            client = await self._get_client()
            kwargs: Dict[str, Any] = {}
            if payload is not None:
                kwargs["json"] = payload
            try:
                resp = await client.request(method, path, **kwargs)
                resp.raise_for_status()
                sm_request_id = ""
                try:
                    sm_request_id = str(resp.json().get("request_id", ""))
                except Exception:
                    pass
                logger.info(
                    "SM %s %s  → %s  sm_req=%s",
                    method, path, resp.status_code, sm_request_id or "(none)",
                )
                return sm_request_id
            except httpx.TimeoutException:
                logger.error(
                    "SM %s %s  → TIMEOUT (%.0fs)", method, path, settings.sm_timeout
                )
                raise
            except httpx.ConnectError as exc:
                logger.error("SM %s %s  → CONNECTION REFUSED (%s)", method, path, exc)
                raise
            except httpx.HTTPStatusError as exc:
                if tolerate_not_found and exc.response.status_code == 404:
                    # Idempotent delete: the resource is already gone — success.
                    logger.info(
                        "SM %s %s  → 404 (already absent, treated as success)",
                        method, path,
                    )
                    return ""
                logger.error(
                    "SM %s %s  → HTTP %s  body=%s",
                    method,
                    path,
                    exc.response.status_code,
                    exc.response.text[:200],
                )
                raise

        return await sm_circuit_breaker.call(
            retry_with_backoff(_attempt, operation_name=f"SM {method} {path}")
        )

    # -- SM endpoints --------------------------------------------------------

    async def create_slice(self, payload: Dict[str, Any]) -> str:
        """
        POST /core/slices

        Returns:
            SM ``request_id`` from the 202 response.
        """
        return await self._request("POST", "/core/slices", payload)

    async def delete_slice(self, slice_id: str) -> str:
        """
        DELETE /core/slices/{slice_id}

        Idempotent: a 404 (slice unknown to the SM) is treated as success.

        Returns:
            SM ``request_id`` from the 202 response, or "" if already absent.
        """
        return await self._request(
            "DELETE", f"/core/slices/{slice_id}", tolerate_not_found=True
        )

    async def associate_slice(self, ue_id: str, payload: Dict[str, Any]) -> str:
        """
        POST /core/ues/{ue_id}/slice-associations

        Returns:
            SM ``request_id`` from the 202 response.
        """
        return await self._request("POST", f"/core/ues/{ue_id}/slice-associations", payload)

    async def dissociate_slice(self, ue_id: str, slice_id: str) -> str:
        """
        DELETE /core/ues/{ue_id}/slice-associations/{slice_id}

        Idempotent: a 404 (association unknown to the SM) is treated as success.

        Returns:
            SM ``request_id`` from the 202 response, or "" if already absent.
        """
        return await self._request(
            "DELETE",
            f"/core/ues/{ue_id}/slice-associations/{slice_id}",
            tolerate_not_found=True,
        )

    async def change_slice(self, ue_id: str, payload: Dict[str, Any]) -> str:
        """
        PATCH /core/ues/{ue_id}/slice-associations

        Returns:
            SM ``request_id`` from the 202 response.
        """
        return await self._request("PATCH", f"/core/ues/{ue_id}/slice-associations", payload)

    async def get_request_status(self, sm_request_id: str) -> Dict[str, Any]:
        """
        GET /operations/{sm_request_id}

        Poll a previously submitted async SM operation.
        This call bypasses the circuit breaker and retry logic — it is a
        lightweight read used only by the background polling task.

        Returns:
            Dict with at least ``{"request_id": str, "state": str}``.
            ``state`` is one of ``"pending"``, ``"published"``, ``"processing"``,
            ``"completed"``, ``"failed"``.

        Raises:
            httpx.HTTPStatusError  - SM returned 4xx/5xx
            httpx.TimeoutException - SM did not respond in time
        """
        client = await self._get_client()
        resp = await client.get(
            f"/operations/{sm_request_id}",
            timeout=settings.sm_health_timeout,
        )
        resp.raise_for_status()
        return resp.json()
