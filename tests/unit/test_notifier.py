from __future__ import annotations

import asyncio

from app.db.schema import init_db
from app.impl import notifier
from app.store.repositories import OperationRepository
from app.store.subscription_store import store

SELF = "/3gpp-as-session-with-qos/v1/myApp/subscriptions/sub-1"

SUBSCRIPTION = {
    "self": SELF,
    "notificationDestination": "http://localhost:9999/callback",
    "ueIpv4Addr": "10.0.0.1",
    "qosReference": "qos_ref_1",
    "events": ["SUCCESSFUL_RESOURCES_ALLOCATION", "FAILED_RESOURCES_ALLOCATION"],
}


def test_success_is_reported_with_the_applied_qos_reference() -> None:
    payload = notifier.build_resource_allocation_notification(SUBSCRIPTION, succeeded=True)

    assert payload == {
        "transaction": SELF,
        "eventReports": [
            {"event": "SUCCESSFUL_RESOURCES_ALLOCATION", "appliedQosRef": "qos_ref_1"}
        ],
    }


def test_failure_is_reported_without_applied_qos_reference() -> None:
    payload = notifier.build_resource_allocation_notification(SUBSCRIPTION, succeeded=False)

    assert payload == {
        "transaction": SELF,
        "eventReports": [{"event": "FAILED_RESOURCES_ALLOCATION"}],
    }


def test_events_the_subscription_did_not_ask_for_are_not_sent() -> None:
    only_failures = {**SUBSCRIPTION, "events": ["FAILED_RESOURCES_ALLOCATION"]}
    no_events = {k: v for k, v in SUBSCRIPTION.items() if k != "events"}

    assert notifier.build_resource_allocation_notification(only_failures, succeeded=True) is None
    assert notifier.build_resource_allocation_notification(no_events, succeeded=False) is None


def test_operation_outcome_is_posted_to_the_notification_destination(sqlite_db, monkeypatch) -> None:
    init_db()
    store.create(
        scs_as_id="myApp",
        subscription_id="sub-1",
        sm_slice_id="s1d000001-internet",
        imsi="999080100001151",
        data=SUBSCRIPTION,
        operation_id="op-1",
    )
    operations = OperationRepository()
    operations.create(
        operation_id="op-1",
        scs_as_id="myApp",
        idempotency_key=None,
        payload_fingerprint="fp-1",
        status="sm_provisioning",
    )
    operations.update_status(operation_id="op-1", status="sm_provisioning", subscription_id="sub-1")

    sent: list = []

    async def _capture(url, payload, operation_id, event):
        sent.append((url, payload, operation_id))

    monkeypatch.setattr(notifier, "_post", _capture)

    asyncio.run(notifier.notify_operation_outcome("op-1", succeeded=True))

    assert sent == [(
        "http://localhost:9999/callback",
        {
            "transaction": SELF,
            "eventReports": [
                {"event": "SUCCESSFUL_RESOURCES_ALLOCATION", "appliedQosRef": "qos_ref_1"}
            ],
        },
        "op-1",
    )]


def _n5_session_with_operation(previous_slice=None):
    from app.impl.policy_authorization_service import N5_OWNER

    init_db()
    resource_uri = "http://translator/npcf-policyauthorization/v1/app-sessions/as-1"
    store.create(
        scs_as_id=N5_OWNER,
        subscription_id="as-1",
        sm_slice_id="placeholder-slice-1",
        imsi="999080100001151",
        data={
            "context": {"ascReqData": {
                "notifUri": "http://sgw:8500/npcf-policyauthorization/v1/notify",
                "suppFeat": "0", "ueIpv4": "10.0.0.1",
                "evSubsc": {"events": [{"event": "FAILED_RESOURCES_ALLOCATION"}],
                            "notifUri": "http://sgw:8500/npcf-policyauthorization/v1/notify"},
            }},
            "resourceUri": resource_uri,
        },
        operation_id="op-n5",
    )
    operations = OperationRepository()
    operations.create(
        operation_id="op-n5",
        scs_as_id=N5_OWNER,
        idempotency_key=None,
        payload_fingerprint="fp-n5",
        status="sm_provisioning",
    )
    operations.update_status(
        operation_id="op-n5", status="sm_provisioning", subscription_id="as-1",
        previous_sm_slice_id=previous_slice,
    )
    return N5_OWNER, resource_uri


def _capture_posts(monkeypatch) -> list:
    sent: list = []

    async def _capture(url, payload, operation_id, event):
        sent.append((url, payload, operation_id))

    monkeypatch.setattr(notifier, "_post", _capture)
    return sent


def test_n5_failed_create_is_terminated_and_forgotten(sqlite_db, monkeypatch) -> None:
    """Like the Open5GS PCF (ADR 0007): TerminationInfo to {notifUri}/terminate."""
    owner, resource_uri = _n5_session_with_operation()
    sent = _capture_posts(monkeypatch)

    asyncio.run(notifier.notify_operation_outcome("op-n5", succeeded=False))

    assert sent == [(
        "http://sgw:8500/npcf-policyauthorization/v1/notify/terminate",
        {"termCause": "INSUFFICIENT_QOS_FLOW_RESOURCES", "resUri": resource_uri},
        "op-n5",
    )]
    assert store.get(owner, "as-1") is None


def test_n5_success_sends_nothing(sqlite_db, monkeypatch) -> None:
    owner, _ = _n5_session_with_operation()
    sent = _capture_posts(monkeypatch)

    asyncio.run(notifier.notify_operation_outcome("op-n5", succeeded=True))

    assert sent == []
    assert store.get(owner, "as-1") is not None


def test_n5_failed_qos_change_sends_nothing_and_keeps_the_session(sqlite_db, monkeypatch) -> None:
    owner, _ = _n5_session_with_operation(previous_slice="placeholder-slice-2")
    sent = _capture_posts(monkeypatch)

    asyncio.run(notifier.notify_operation_outcome("op-n5", succeeded=False))

    assert sent == []
    assert store.get(owner, "as-1") is not None
