# coding: utf-8

"""
SM Operation Poller
===================

Background coroutine that polls ``GET /operations/{request_id}`` for the
Slice Manager write of one translator operation (an associate, or a move for
a QoS change) until it reaches a terminal state, then:

  1. Updates the local ``operations`` record to ``"completed"`` or ``"failed"``.
  2. After a failed QoS change, puts the session back on its previous slice
     (docs/adr/0006).
  3. Notifies the session's application (``app.impl.notifier``): a 3GPP
     ``UserPlaneNotificationData`` (29.122), or for N5 a termination request
     after a failed create only, as the Open5GS PCF does (docs/adr/0007).

SM states
---------
``pending`` → ``published`` → ``processing`` → ``completed`` / ``failed``.
Only ``completed`` and ``failed`` are terminal: ``published`` merely means the
message reached Kafka, before any worker touched the network. The SM
aggregates per-component progress (``core``, plus ``ran`` for slice
operations) into the top-level ``state``; on failure the component ``error``
strings are copied into the local operation record.

Usage
-----
Launch immediately after the SM write returns its ``request_id``::

    import asyncio
    from app.impl.sm_poller import poll_sm_request

    asyncio.create_task(
        poll_sm_request(sm_request_id=associate_req_id, operation_id=operation_id)
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

Notification delivery is best-effort (see ``app.impl.notifier``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from app.config.settings import settings
from app.impl.notifier import notify_operation_outcome
from app.impl.sm_client import SliceManagerClient
from app.store.repositories import OperationRepository
from app.store.subscription_store import store

logger = logging.getLogger(__name__)

TERMINAL_STATES = frozenset({"completed", "failed"})


async def poll_sm_request(sm_request_id: str, operation_id: str) -> None:
    """
    Poll the SM until *sm_request_id* is terminal.

    Args:
        sm_request_id:  UUID returned by the SM in its 202 response.
        operation_id:   Local operation ID used to update the DB record and
                        to find the subscription to notify. Pass an empty
                        string for a write not tracked locally (dissociate):
                        it is polled for logging only.
    """
    sm_client = SliceManagerClient()
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

        status = await _fetch_status(sm_client, sm_request_id, elapsed)
        if status is not None:  # None: transient error, retried on the next round
            state = str(status.get("state", ""))
            logger.debug(
                "SM request state  sm_req=%s  state=%s  elapsed=%.0fs",
                sm_request_id,
                state,
                elapsed,
            )

            if state == "completed":
                logger.info(
                    "SM provisioning confirmed  sm_req=%s  op=%s  elapsed=%.0fs",
                    sm_request_id,
                    operation_id,
                    elapsed,
                )
                await _finish(operation_id, "completed")
                return

            if state == "failed":
                await _finish(
                    operation_id,
                    "failed",
                    error=_failure_reason(sm_request_id, status),
                )
                return

        # Still in flight — back off and continue
        interval = min(interval * 2, settings.sm_poll_max_interval)

    # ── Deadline exceeded ──────────────────────────────────────────────────
    logger.error(
        "SM poller timed out  sm_req=%s  op=%s  timeout=%.0fs",
        sm_request_id,
        operation_id,
        timeout,
    )
    await _finish(
        operation_id,
        "failed",
        error=f"SM polling timeout after {timeout:.0f}s",
    )


async def _finish(
    operation_id: str,
    status: str,
    error: Optional[str] = None,
) -> None:
    """Record the terminal status locally, undo a failed QoS change, and
    notify the application."""
    if error:
        logger.error("SM provisioning failed  op=%s  error=%s", operation_id, error)
    if operation_id:
        repo = OperationRepository()
        repo.update_status(
            operation_id=operation_id,
            status=status,
            error=error,
        )
        if status == "failed":
            _restore_previous_slice(repo.get(operation_id))
        await notify_operation_outcome(operation_id, succeeded=status == "completed")


def _restore_previous_slice(operation: Optional[Dict[str, Any]]) -> None:
    """After a failed QoS change, put the session back on the slice it left
    (docs/adr/0006, decision 4). A create has no previous slice: nothing to do."""
    if not operation or not operation.get("previous_sm_slice_id"):
        return
    if not operation.get("subscription_id") or not operation.get("sm_slice_id"):
        return
    restored = store.restore_slice(
        operation["scs_as_id"],
        operation["subscription_id"],
        expected_slice_id=operation["sm_slice_id"],
        previous_slice_id=operation["previous_sm_slice_id"],
    )
    if not restored:
        logger.info(
            "QoS change failed but the session moved on (or is gone): slice kept  op=%s",
            operation["operation_id"],
        )


def _failure_reason(sm_request_id: str, status: Dict[str, Any]) -> str:
    """Build a readable error from the SM's per-component breakdown."""
    operation = status.get("operation") or "operation"
    components = status.get("components") or {}
    details = [
        f"{name}: {entry.get('error')}"
        for name, entry in components.items()
        if isinstance(entry, dict) and entry.get("state") == "failed" and entry.get("error")
    ]
    reason = "; ".join(details) if details else "SM reported state=failed"
    return f"SM {operation} {sm_request_id} failed: {reason}"


async def _fetch_status(
    sm_client: SliceManagerClient,
    sm_request_id: str,
    elapsed: float,
) -> Optional[Dict[str, Any]]:
    """
    Call ``GET /operations/{id}`` and return the SM status body.
    Returns ``None`` on any transient error so the poller can back off.
    """
    try:
        return await sm_client.get_request_status(sm_request_id)
    except Exception as exc:
        logger.warning(
            "SM poll attempt failed  sm_req=%s  elapsed=%.0fs  error=%s",
            sm_request_id,
            elapsed,
            exc,
        )
        return None
