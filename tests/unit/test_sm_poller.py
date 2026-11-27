from __future__ import annotations

import asyncio
from typing import Dict, List

import pytest

from app.config.settings import settings
from app.db.schema import init_db
from app.impl import sm_poller
from app.impl.sm_client import SliceManagerClient
from app.store.repositories import OperationRepository


@pytest.fixture
def fast_polling(monkeypatch):
    monkeypatch.setattr(settings, "sm_poll_initial_interval", 0.001)
    monkeypatch.setattr(settings, "sm_poll_max_interval", 0.001)
    monkeypatch.setattr(settings, "sm_poll_timeout", 1.0)


def _install_sm_states(monkeypatch, states: Dict[str, List[dict]]) -> None:
    """Each request_id answers its queued status bodies in order; the last
    one repeats once the queue is drained."""

    async def _get_request_status(self, sm_request_id):
        queue = states[sm_request_id]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(SliceManagerClient, "get_request_status", _get_request_status)


def _new_operation(operation_id: str) -> OperationRepository:
    init_db()
    repo = OperationRepository()
    repo.create(
        operation_id=operation_id,
        scs_as_id="myApp",
        idempotency_key=None,
        payload_fingerprint=f"fp-{operation_id}",
        status="sm_provisioning",
    )
    return repo


def test_published_is_not_terminal(sqlite_db, fast_polling, monkeypatch) -> None:
    """``published`` only means the message reached Kafka: keep polling."""
    repo = _new_operation("op-1")
    _install_sm_states(monkeypatch, {
        "req-1": [
            {"state": "published"},
            {"state": "processing"},
            {"state": "completed"},
        ],
    })

    asyncio.run(sm_poller.poll_sm_request(sm_request_id="req-1", operation_id="op-1"))

    assert repo.get("op-1")["status"] == "completed"


def test_failure_records_the_failed_components(sqlite_db, fast_polling, monkeypatch) -> None:
    repo = _new_operation("op-3")
    _install_sm_states(monkeypatch, {
        "assoc": [{"state": "processing"}, {
            "state": "failed",
            "operation": "associate_slice",
            "components": {
                "core": {"state": "failed", "error": "UDM rejected ADD DNNQOSTPL"},
            },
        }],
    })

    asyncio.run(sm_poller.poll_sm_request(sm_request_id="assoc", operation_id="op-3"))

    op = repo.get("op-3")
    assert op["status"] == "failed"
    assert "associate_slice" in op["error"]
    assert "core: UDM rejected ADD DNNQOSTPL" in op["error"]


def test_timeout_marks_operation_failed(sqlite_db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sm_poll_initial_interval", 0.001)
    monkeypatch.setattr(settings, "sm_poll_max_interval", 0.001)
    monkeypatch.setattr(settings, "sm_poll_timeout", 0.01)
    repo = _new_operation("op-4")
    _install_sm_states(monkeypatch, {"req-4": [{"state": "processing"}]})

    asyncio.run(sm_poller.poll_sm_request(sm_request_id="req-4", operation_id="op-4"))

    op = repo.get("op-4")
    assert op["status"] == "failed"
    assert "timeout" in op["error"]


def test_terminal_state_notifies_the_application(sqlite_db, fast_polling, monkeypatch) -> None:
    _new_operation("op-5")
    _new_operation("op-6")
    _install_sm_states(monkeypatch, {
        "ok": [{"state": "completed"}],
        "ko": [{"state": "failed", "operation": "associate_slice", "components": {}}],
    })
    notified: list = []

    async def _capture(operation_id, succeeded):
        notified.append((operation_id, succeeded))

    monkeypatch.setattr(sm_poller, "notify_operation_outcome", _capture)

    asyncio.run(sm_poller.poll_sm_request(sm_request_id="ok", operation_id="op-5"))
    asyncio.run(sm_poller.poll_sm_request(sm_request_id="ko", operation_id="op-6"))

    assert notified == [("op-5", True), ("op-6", False)]


# ── QoS change: a failed move puts the session back (docs/adr/0006) ───────

def _session_on(slice_id: str) -> None:
    from app.store.subscription_store import store

    store.create(
        scs_as_id="myApp", subscription_id="sub-1", sm_slice_id=slice_id,
        imsi="999080100001151", data={"qosReference": "qos_ref_2"},
    )


def _change_operation(repo: OperationRepository, operation_id: str) -> None:
    repo.update_status(
        operation_id=operation_id, status="sm_provisioning", subscription_id="sub-1",
        sm_slice_id="placeholder-slice-2", previous_sm_slice_id="placeholder-slice-1",
    )


def _slice_of_session() -> str:
    from app.store.subscription_store import store

    return store.get("myApp", "sub-1").sm_slice_id


def _no_notifications(monkeypatch) -> list:
    notified: list = []

    async def _capture(operation_id, succeeded):
        notified.append((operation_id, succeeded))

    monkeypatch.setattr(sm_poller, "notify_operation_outcome", _capture)
    return notified


def test_failed_change_restores_the_previous_slice(sqlite_db, fast_polling, monkeypatch) -> None:
    repo = _new_operation("op-c1")
    _session_on("placeholder-slice-2")
    _change_operation(repo, "op-c1")
    _install_sm_states(monkeypatch, {"move": [{"state": "failed", "components": {}}]})
    notified = _no_notifications(monkeypatch)

    asyncio.run(sm_poller.poll_sm_request(sm_request_id="move", operation_id="op-c1"))

    assert repo.get("op-c1")["status"] == "failed"
    assert _slice_of_session() == "placeholder-slice-1"
    assert notified == [("op-c1", False)]


def test_failed_change_does_not_undo_a_later_change(sqlite_db, fast_polling, monkeypatch) -> None:
    repo = _new_operation("op-c2")
    _session_on("placeholder-slice-3")  # moved again since op-c2 was accepted
    _change_operation(repo, "op-c2")
    _install_sm_states(monkeypatch, {"move": [{"state": "failed", "components": {}}]})
    _no_notifications(monkeypatch)

    asyncio.run(sm_poller.poll_sm_request(sm_request_id="move", operation_id="op-c2"))

    assert _slice_of_session() == "placeholder-slice-3"


def test_completed_change_keeps_the_new_slice(sqlite_db, fast_polling, monkeypatch) -> None:
    repo = _new_operation("op-c3")
    _session_on("placeholder-slice-2")
    _change_operation(repo, "op-c3")
    _install_sm_states(monkeypatch, {"move": [{"state": "completed"}]})
    notified = _no_notifications(monkeypatch)

    asyncio.run(sm_poller.poll_sm_request(sm_request_id="move", operation_id="op-c3"))

    assert _slice_of_session() == "placeholder-slice-2"
    assert notified == [("op-c3", True)]


def test_failed_create_leaves_the_session_alone(sqlite_db, fast_polling, monkeypatch) -> None:
    repo = _new_operation("op-c4")
    _session_on("placeholder-slice-1")
    repo.update_status(
        operation_id="op-c4", status="sm_provisioning", subscription_id="sub-1",
        sm_slice_id="placeholder-slice-1",
    )
    _install_sm_states(monkeypatch, {"assoc": [{"state": "failed", "components": {}}]})
    _no_notifications(monkeypatch)

    asyncio.run(sm_poller.poll_sm_request(sm_request_id="assoc", operation_id="op-c4"))

    assert _slice_of_session() == "placeholder-slice-1"
