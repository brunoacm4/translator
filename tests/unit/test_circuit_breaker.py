"""The SM circuit breaker opens only when the SM is unreachable or failing."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpen,
    CircuitState,
    counts_as_sm_failure,
)

THRESHOLD = 3


def _breaker(recovery_timeout: float = 30.0) -> CircuitBreaker:
    return CircuitBreaker(
        name="test-sm",
        failure_threshold=THRESHOLD,
        recovery_timeout=recovery_timeout,
        is_failure=counts_as_sm_failure,
    )


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://sm.test/core/ues/1/slice-associations")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=response)


async def _raising(exc: Exception) -> str:
    raise exc


async def _ok() -> str:
    return "req-1"


def _call(breaker: CircuitBreaker, exc: Exception) -> None:
    with pytest.raises(type(exc)):
        asyncio.run(breaker.call(_raising(exc)))


def test_sm_4xx_answers_never_open_the_circuit() -> None:
    breaker = _breaker()
    for _ in range(THRESHOLD * 2):
        _call(breaker, _status_error(404))

    assert breaker.state is CircuitState.CLOSED
    assert breaker.failure_count == 0


def test_sm_5xx_answers_open_the_circuit() -> None:
    breaker = _breaker()
    for _ in range(THRESHOLD):
        _call(breaker, _status_error(503))

    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CircuitBreakerOpen):
        asyncio.run(breaker.call(_ok()))


def test_network_errors_open_the_circuit() -> None:
    breaker = _breaker()
    _call(breaker, httpx.ConnectError("refused"))
    _call(breaker, httpx.ReadTimeout("timed out"))
    _call(breaker, httpx.ConnectTimeout("timed out"))

    assert breaker.state is CircuitState.OPEN


def test_a_4xx_resets_the_failure_count() -> None:
    breaker = _breaker()
    for _ in range(THRESHOLD - 1):
        _call(breaker, httpx.ConnectError("refused"))
    _call(breaker, _status_error(409))
    _call(breaker, httpx.ConnectError("refused"))

    assert breaker.state is CircuitState.CLOSED
    assert breaker.failure_count == 1


def test_a_4xx_while_half_open_closes_the_circuit() -> None:
    breaker = _breaker(recovery_timeout=0.0)
    for _ in range(THRESHOLD):
        _call(breaker, httpx.ConnectError("refused"))
    assert breaker.state is CircuitState.OPEN

    # recovery_timeout 0: the next call probes the SM, which answers 404
    _call(breaker, _status_error(404))

    assert breaker.state is CircuitState.CLOSED
