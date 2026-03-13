# coding: utf-8

"""
    Retry with Exponential Backoff and Full Jitter

    Wraps SM calls so that transient network hiccups (brief SM restart,
    momentary packet loss) do not immediately surface as 502 errors to the NEF.

    Retry policy
    ------------
    - Only transient errors are retried: ``TimeoutException``, ``ConnectError``
    - HTTP 4xx / 5xx responses are NOT retried — they represent SM-reported
      problems that will not improve on retry and should be surfaced immediately
    - ``CircuitBreakerOpen`` is NOT retried — the circuit is intentionally open

    Backoff formula (full jitter)
    -----------------------------
    For attempt ``n`` (1-based), the wait before attempt ``n+1`` is::

        ceiling = min(max_wait, min_wait * 2^(n-1))
        wait    = random.uniform(0, ceiling)

    Full jitter is preferred over fixed or equal jitter because it minimises
    thundering-herd effects when multiple NEF retries arrive simultaneously.

    Example with defaults (min=0.5s, max=3.0s)
    -------------------------------------------
    - After attempt 1 failure: wait 0 – 0.5 s
    - After attempt 2 failure: wait 0 – 1.0 s
    - After attempt 3 failure: wait 0 – 2.0 s
    - After attempt 4 failure: wait 0 – 3.0 s  (cap reached)

    Configuration
    -------------
    All values are read from ``settings`` (env vars / .env):
        RETRY_MAX_ATTEMPTS   default 2
        RETRY_MIN_WAIT       default 0.5
        RETRY_MAX_WAIT       default 3.0
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable, Tuple, Type, TypeVar

import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Exceptions that warrant a retry.  Anything else (including
# HTTPStatusError and CircuitBreakerOpen) propagates immediately.
RETRIABLE: Tuple[Type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
)


async def retry_with_backoff(
    coro_factory: Callable[[], Awaitable[T]],
    operation_name: str = "SM call",
) -> T:
    """
    Execute ``coro_factory()`` with automatic retry on transient failures.

    ``coro_factory`` must be a *callable* that returns a fresh coroutine each
    time it is called, because a coroutine can only be awaited once::

        await retry_with_backoff(
            lambda: client.post("/core/slice/create", json=payload),
            operation_name="create_slice",
        )

    Args:
        coro_factory:   Zero-argument callable returning a coroutine.
        operation_name: Label used in log messages.

    Returns:
        Whatever the coroutine returns on success.

    Raises:
        The last exception if all attempts are exhausted.
    """
    max_attempts = settings.retry_max_attempts
    min_wait = settings.retry_min_wait
    max_wait = settings.retry_max_wait

    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await coro_factory()
        except RETRIABLE as exc:
            last_exc = exc
            if attempt == max_attempts:
                logger.error(
                    "%s failed after %d attempt(s): %s",
                    operation_name,
                    max_attempts,
                    exc,
                )
                raise

            # Full-jitter exponential backoff
            ceiling = min(max_wait, min_wait * (2 ** (attempt - 1)))
            wait = random.uniform(0, ceiling)

            logger.warning(
                "%s attempt %d/%d failed (%s). Retrying in %.2fs…",
                operation_name,
                attempt,
                max_attempts,
                type(exc).__name__,
                wait,
            )
            await asyncio.sleep(wait)

    # Should never reach here — loop always raises or returns
    raise RuntimeError("retry_with_backoff: control flow error") from last_exc
