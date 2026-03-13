# coding: utf-8

"""
    Circuit Breaker

    Protects the translator from cascading failures when the Slice Manager
    becomes unavailable (e.g. during maintenance windows at Porto de Aveiro).

    State machine
    -------------

        ┌─────────────────────────────┐
        │                             │  failure_count >= threshold
        │         CLOSED              │ ─────────────────────────────►  OPEN
        │   (normal operation)        │                                    │
        └─────────────────────────────┘                                    │
                     ▲                                          recovery_timeout
                     │                                              elapsed
                     │  probe call succeeds                          │
                     │                                               ▼
        ┌─────────────────────────────┐                  ┌──────────────────────┐
        │        HALF_OPEN            │ ◄────────────────│         OPEN         │
        │   (single probe allowed)    │                  │  (fail fast, no SM   │
        │                             │  probe fails     │      calls made)      │
        └─────────────────────────────┘ ─────────────────►└──────────────────────┘

    Usage
    -----
    The module exposes a ready-to-use singleton ``sm_circuit_breaker`` that
    ``SliceManagerClient`` wraps every outbound call with::

        result = await sm_circuit_breaker.call(client.post("/core/slice/create", ...))

    When OPEN, ``call()`` raises ``CircuitBreakerOpen`` immediately (before any
    network I/O) so the NEF gets a fast 503 instead of waiting 30 s for a timeout.

    Configuration
    -------------
    All thresholds are read from ``settings`` (env vars / .env):
        CB_FAILURE_THRESHOLD   default 5
        CB_RECOVERY_TIMEOUT    default 30.0
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Awaitable, TypeVar

from app.config.settings import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ── States ─────────────────────────────────────────────────────────────


class CircuitState(Enum):
    CLOSED = "closed"        # Normal operation — calls go through
    OPEN = "open"            # SM appears down — calls rejected immediately
    HALF_OPEN = "half_open"  # Recovery probe — one call allowed through


# ── Exception ──────────────────────────────────────────────────────────


class CircuitBreakerOpen(Exception):
    """
    Raised when a call is attempted while the circuit is OPEN.

    The caller (``SliceManagerClient._post``) maps this to HTTP 503.
    """

    def __init__(self, name: str) -> None:
        super().__init__(
            f"Circuit '{name}' is OPEN — SM is unavailable. "
            f"Calls are being rejected to prevent cascading failures."
        )
        self.circuit_name = name


# ── Core implementation ────────────────────────────────────────────────


class CircuitBreaker:
    """
    Async-safe circuit breaker.

    Thread safety is provided by ``asyncio.Lock``.  This works correctly
    in the single-threaded uvicorn event loop used in production.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int,
        recovery_timeout: float,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self._state = CircuitState.CLOSED
        self._failure_count: int = 0
        self._last_failure_time: float = 0.0
        self._lock = asyncio.Lock()

    # -- public interface ----------------------------------------------------

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    async def call(self, coro: Awaitable[T]) -> T:
        """
        Execute the awaitable through the circuit breaker.

        Args:
            coro: An already-created coroutine to execute.

        Raises:
            CircuitBreakerOpen: If the circuit is OPEN.
            Any exception raised by ``coro`` (also recorded as a failure).
        """
        async with self._lock:
            self._maybe_transition_to_half_open()

            if self._state == CircuitState.OPEN:
                raise CircuitBreakerOpen(self.name)

        # Execute outside the lock so other requests are not blocked
        try:
            result = await coro
            await self._record_success()
            return result
        except Exception:
            await self._record_failure()
            raise

    def status(self) -> dict:
        """Return a dict suitable for inclusion in the /health response."""
        return {
            "state": self._state.value,
            "failure_count": self._failure_count,
            "threshold": self.failure_threshold,
        }

    # -- internal state transitions -----------------------------------------

    def _maybe_transition_to_half_open(self) -> None:
        """Called under lock — transition OPEN → HALF_OPEN if timeout elapsed."""
        if (
            self._state == CircuitState.OPEN
            and time.monotonic() - self._last_failure_time >= self.recovery_timeout
        ):
            self._state = CircuitState.HALF_OPEN
            logger.info(
                "Circuit '%s' → HALF_OPEN (probing SM after %.0fs)",
                self.name,
                self.recovery_timeout,
            )

    async def _record_success(self) -> None:
        async with self._lock:
            previous = self._state
            self._failure_count = 0
            if previous == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                logger.info("Circuit '%s' → CLOSED (SM recovered)", self.name)

    async def _record_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logger.warning("Circuit '%s' → OPEN (recovery probe failed)", self.name)
            elif (
                self._state == CircuitState.CLOSED
                and self._failure_count >= self.failure_threshold
            ):
                self._state = CircuitState.OPEN
                logger.warning(
                    "Circuit '%s' → OPEN (%d/%d failures reached threshold)",
                    self.name,
                    self._failure_count,
                    self.failure_threshold,
                )


# ── Module-level singleton ─────────────────────────────────────────────
# Shared across all SliceManagerClient instances so the failure count
# is consistent regardless of how many requests are in flight.

sm_circuit_breaker = CircuitBreaker(
    name="slice-manager",
    failure_threshold=settings.cb_failure_threshold,
    recovery_timeout=settings.cb_recovery_timeout,
)
