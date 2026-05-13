# coding: utf-8

"""
SM Operation Poller
===================

Background coroutine that polls ``GET /operations/{request_id}`` until
the Slice Manager reports a terminal state (``"completed"``, ``"failed"``,
or ``"published"`` which the sandbox uses as its terminal accepted state),
then:

  1. Updates the local ``operations`` record to ``"completed"`` or ``"failed"``.
  2. Optionally sends an HTTP callback to the NEF's ``notificationDestination``
     with the final status.

Usage
-----
Launch immediately after a write operation returns a ``request_id``::

    import asyncio
    from app.impl.sm_poller import poll_sm_request

    asyncio.create_task(
        poll_sm_request(
            sm_request_id=sm_req_id,
            operation_id=operation_id,
            notification_url=body.notificationDestination,
            subscription_id=subscription_id,
        )
    )

Startup resume
--------------
``main.py`` queries for operations in ``sm_provisioning`` state at startup
and relaunches a polling task for each, so in-progress provisioning survives
process restarts (OOM kills, rolling deploys, etc.).

Polling strategy
----------------
Exponential backoff with a configurable ceiling::

    interval → min(interval * 2, SM_POLL_MAX_INTERVAL)

Controlled by three environment variables (see ``settings``):

    SM_POLL_INITIAL_INTERVAL  (default 2.0 s)
    SM_POLL_MAX_INTERVAL      (default 30.0 s)
    SM_POLL_TIMEOUT           (default 300.0 s — 5 minutes)

Notification delivery is best-effort: failures are logged but do not
affect the operation status update.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from app.config.settings import settings
from app.impl.sm_client import SliceManagerClient
from app.store.repositories import OperationRepository

logger = logging.getLogger(__name__)


async def poll_sm_request(
    sm_request_id: str,
    operation_id: str,
    notification_url: Optional[str] = None,
    subscription_id: Optional[str] = None,
) -> None:
    """
    Poll the SM until *request_id* reaches a terminal state.

    Args:
        sm_request_id:    UUID returned by the SM in its 202 response.
        operation_id:     Local operation ID used to update the DB record.
                          Pass an empty string to skip DB updates (e.g. for
                          update/patch/delete operations not tracked locally).
        notification_url: URL to POST when the operation reaches a terminal
                          state.  ``None`` disables the callback.
        subscription_id:  Included in the callback body so the client can
                          correlate the notification with its subscription.
    """
    sm_client = SliceManagerClient()
    operation_repo = OperationRepository()

    interval = settings.sm_poll_initial_interval
    timeout = settings.sm_poll_timeout
    elapsed = 0.0

    logger.info(
        "SM poller started  sm_req=%s  op=%s  timeout=%.0fs",
        sm_request_id,
        operation_id,
        timeout,
    )

    while elapsed < timeout:
        await asyncio.sleep(interval)
        elapsed += interval

        state = await _fetch_state(sm_client, sm_request_id, elapsed)
        if state is None:
            # Transient error — back off and retry
            interval = min(interval * 2, settings.sm_poll_max_interval)
            continue

        logger.debug(
            "SM request state  sm_req=%s  state=%s  elapsed=%.0fs",
            sm_request_id,
            state,
            elapsed,
        )

        if state in ("completed", "published"):
            if operation_id:
                operation_repo.update_status(
                    operation_id=operation_id,
                    status="completed",
                )
            logger.info(
                "SM provisioning confirmed  sm_req=%s  op=%s  state=%s  elapsed=%.0fs",
                sm_request_id,
                operation_id,
                state,
                elapsed,
            )
            if notification_url:
                await _send_notification(
                    notification_url, operation_id, "completed", subscription_id
                )
            return

        if state == "failed":
            if operation_id:
                operation_repo.update_status(
                    operation_id=operation_id,
                    status="failed",
                    error="SM reported state=failed",
                )
            logger.error(
                "SM provisioning failed  sm_req=%s  op=%s  elapsed=%.0fs",
                sm_request_id,
                operation_id,
                elapsed,
            )
            if notification_url:
                await _send_notification(
                    notification_url, operation_id, "failed", subscription_id
                )
            return

        # Still pending — back off and continue
        interval = min(interval * 2, settings.sm_poll_max_interval)

    # ── Deadline exceeded ──────────────────────────────────────────────────
    logger.error(
        "SM poller timed out  sm_req=%s  op=%s  timeout=%.0fs",
        sm_request_id,
        operation_id,
        timeout,
    )
    if operation_id:
        operation_repo.update_status(
            operation_id=operation_id,
            status="failed",
            error=f"SM polling timeout after {timeout:.0f}s",
        )
    if notification_url:
        await _send_notification(
            notification_url, operation_id, "failed", subscription_id
        )


async def _fetch_state(
    sm_client: SliceManagerClient,
    sm_request_id: str,
    elapsed: float,
) -> Optional[str]:
    """
    Call ``GET /core/requests/{id}`` and return the ``state`` string.
    Returns ``None`` on any transient error so the poller can back off.
    """
    try:
        result = await sm_client.get_request_status(sm_request_id)
        return str(result.get("state", ""))
    except Exception as exc:
        logger.warning(
            "SM poll attempt failed  sm_req=%s  elapsed=%.0fs  error=%s",
            sm_request_id,
            elapsed,
            exc,
        )
        return None


async def _send_notification(
    url: str,
    operation_id: str,
    status: str,
    subscription_id: Optional[str],
) -> None:
    """
    POST the operation outcome to the NEF callback URL.

    Delivery is best-effort: failures are logged but never re-raised so
    a notification failure never corrupts the operation state.
    """
    payload: dict = {"operationId": operation_id, "status": status}
    if subscription_id:
        payload["subscriptionId"] = subscription_id

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            logger.info(
                "Callback delivered  url=%s  op=%s  status=%s  → %d",
                url,
                operation_id,
                status,
                resp.status_code,
            )
    except Exception as exc:
        logger.warning(
            "Callback delivery failed (best-effort)  url=%s  op=%s  error=%s",
            url,
            operation_id,
            exc,
        )
