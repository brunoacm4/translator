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
        │  (calls go through; the     │                  │  (fail fast, no SM   │
        │   first outcome decides)    │  probe fails     │      calls made)      │
        └─────────────────────────────┘ ─────────────────►└──────────────────────┘

    What counts as a failure
    ------------------------
    The breaker tracks whether the SM is reachable, not whether one request
    was valid. For the SM (``counts_as_sm_failure``), only network errors,
    timeouts and ``5xx`` answers count. A ``4xx`` means the SM is up and
    rejected that request (an unknown slice, for example): it counts as a
    success, so bad requests cannot open the circuit for good ones.

    Usage
    -----
    The module exposes a ready-to-use singleton ``sm_circuit_breaker`` that
    ``SliceManagerClient`` wraps every outbound call with::

        result = await sm_circuit_breaker.call(client.request("POST", path, json=payload))

    When OPEN, ``call()`` raises ``CircuitBreakerOpen`` immediately (before any
    network I/O) so the NEF gets a fast 503 instead of waiting for the SM timeout.

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
from typing import Awaitable, Callable, TypeVar

import httpx

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

    The caller (``SessionCore.call_sm``) maps this to HTTP 503.
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
    in the single-threaded Hypercorn event loop used in production.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int,
        recovery_timeout: float,
        is_failure: Callable[[Exception], bool] = lambda exc: True,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._is_failure = is_failure

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
            Any exception raised by ``coro``. It is recorded as a failure only
            when ``is_failure`` says so, and as a success otherwise.
        """
        async with self._lock:
            self._maybe_transition_to_half_open()

            if self._state == CircuitState.OPEN:
                # The call never runs: close its coroutine, or Python warns
                # that it was never awaited.
                close = getattr(coro, "close", None)
                if close is not None:
                    close()
                raise CircuitBreakerOpen(self.name)

        # Execute outside the lock so other requests are not blocked
        try:
            result = await coro
        except Exception as exc:
            if self._is_failure(exc):
                await self._record_failure()
            else:
                await self._record_success()
            raise
        await self._record_success()
        return result

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


def counts_as_sm_failure(exc: Exception) -> bool:
    """Whether an SM call error means the SM is unreachable or failing.

    Network errors, timeouts and ``5xx`` answers do. A ``4xx`` does not: the
    SM answered and rejected that one request.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return True


# ── Module-level singleton ─────────────────────────────────────────────
# Shared across all SliceManagerClient instances so the failure count
# is consistent regardless of how many requests are in flight.

sm_circuit_breaker = CircuitBreaker(
    name="slice-manager",
    failure_threshold=settings.cb_failure_threshold,
    recovery_timeout=settings.cb_recovery_timeout,
    is_failure=counts_as_sm_failure,
)
