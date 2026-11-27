# coding: utf-8

"""
Application notifications (3GPP TS 29.122 ``UserPlaneNotificationData``).

When an operation reaches a terminal state, the application that owns the
subscription is told at its ``notificationDestination``::

    {
      "transaction": "/3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions/{id}",
      "eventReports": [{"event": "SUCCESSFUL_RESOURCES_ALLOCATION",
                        "appliedQosRef": "qos_ref_1"}]
    }

Only events the subscription listed in ``events`` are sent, as the spec
requires. Delivery is best-effort: failures are logged and never change the
operation state.

N5 sessions (TS 29.514) behave like the Open5GS PCF (docs/adr/0007): only a
failed create is notified, as a termination request to
``{ascReqData.notifUri}/terminate``, and the session is then forgotten (the
Open5GS PCF drops app sessions with the PDU session). A success or a failed
QoS change sends nothing; see
``app.impl.policy_authorization_service.build_termination_request``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

from app.models.nef.common import (
    UserPlaneEvent,
    UserPlaneEventReport,
    UserPlaneNotificationData,
)
from app.store.repositories import OperationRepository
from app.store.subscription_store import store

logger = logging.getLogger(__name__)


def build_resource_allocation_notification(
    subscription: Dict[str, Any],
    succeeded: bool,
) -> Optional[Dict[str, Any]]:
    """
    Build the notification for the outcome of a resource allocation.

    Returns ``None`` when the subscription did not subscribe to that event.
    ``appliedQosRef`` is only reported on success, as in TS 29.122.
    """
    event = (
        UserPlaneEvent.SUCCESSFUL_RESOURCES_ALLOCATION
        if succeeded
        else UserPlaneEvent.FAILED_RESOURCES_ALLOCATION
    )
    if event.value not in (subscription.get("events") or []):
        return None

    report = UserPlaneEventReport(
        event=event,
        appliedQosRef=subscription.get("qosReference") if succeeded else None,
    )
    notification = UserPlaneNotificationData(
        transaction=subscription.get("self", ""),
        eventReports=[report],
    )
    return notification.model_dump(mode="json", exclude_none=True)


async def notify_operation_outcome(operation_id: str, succeeded: bool) -> None:
    """Notify the subscription's application of a finished operation."""
    # local import avoids circular (policy_authorization_service → session_core → sm_poller → here)
    from app.impl.policy_authorization_service import N5_OWNER

    operation = OperationRepository().get(operation_id)
    if not operation or not operation.get("subscription_id"):
        return
    record = store.get(operation["scs_as_id"], operation["subscription_id"])
    if record is None:
        # The subscription was deleted while the operation was in flight.
        return

    if operation["scs_as_id"] == N5_OWNER:
        await _terminate_failed_n5_session(operation, record, succeeded)
        return

    url = record.subscription_data.get("notificationDestination")
    payload = build_resource_allocation_notification(record.subscription_data, succeeded)
    if not url or payload is None:
        logger.debug(
            "No notification for op=%s (destination=%s, event subscribed=%s)",
            operation_id, bool(url), payload is not None,
        )
        return
    reports = payload.get("eventReports") or [{}]
    await _post(url, payload, operation_id, reports[0].get("event"))


async def _terminate_failed_n5_session(operation: Dict[str, Any], record, succeeded: bool) -> None:
    """N5, like the Open5GS PCF (docs/adr/0007): terminate a session whose
    create failed, then forget it. A success, or a failed QoS change (already
    reverted by the poller), sends nothing."""
    # local import avoids circular (policy_authorization_service → session_core → sm_poller → here)
    from app.impl.policy_authorization_service import N5_OWNER, build_termination_request

    if succeeded or operation.get("previous_sm_slice_id"):
        return
    url, payload = build_termination_request(record.subscription_data)
    if url and payload is not None:
        await _post(url, payload, operation["operation_id"], "TERMINATE")
    store.delete(N5_OWNER, record.subscription_id)
    logger.info(
        "N5 session terminated after a failed create  appSession=%s  op=%s",
        record.subscription_id, operation["operation_id"],
    )


async def _post(url: str, payload: Dict[str, Any], operation_id: str, event: Optional[str]) -> None:
    """POST the notification; failures are logged, never raised."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
        logger.info(
            "Notification delivered  url=%s  op=%s  event=%s  → %d",
            url, operation_id, event, resp.status_code,
        )
    except Exception as exc:
        logger.warning(
            "Notification delivery failed (best-effort)  url=%s  op=%s  event=%s  error=%s",
            url, operation_id, event, exc,
        )
