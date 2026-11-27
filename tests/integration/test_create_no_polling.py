from __future__ import annotations

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.db.schema import init_db
from app.main import app


REQUEST_BODY = {
    "notificationDestination": "http://localhost:9999/callback",
    "qosReference": "qos_ref_1",
    "ueIpv4Addr": "10.0.0.1",
    "dnn": "internet",
}


def _capture_polls(monkeypatch, poll_calls: list) -> None:
    async def _fake_poll(*args, **kwargs):
        poll_calls.append(kwargs)

    # Patch the symbol as imported into the core module
    monkeypatch.setattr("app.impl.session_core.poll_sm_request", _fake_poll)


def _create_and_get_operation(client: TestClient, idem_key: str) -> dict:
    r1 = client.post(
        "/3gpp-as-session-with-qos/v1/myApp/subscriptions",
        json=REQUEST_BODY,
        headers={"Idempotency-Key": idem_key},
    )
    assert r1.status_code == 201

    # A duplicate request echoes the original operationId so we can inspect it
    r2 = client.post(
        "/3gpp-as-session-with-qos/v1/myApp/subscriptions",
        json=REQUEST_BODY,
        headers={"Idempotency-Key": idem_key},
    )
    assert r2.status_code == 202
    op_id = r2.json()["operationId"]

    op = client.get(f"/3gpp-as-session-with-qos/v1/operations/{op_id}")
    assert op.status_code == 200
    return op.json()


def test_create_without_polling_marks_operation_completed(sqlite_db, fake_sm, monkeypatch) -> None:
    """With SM polling disabled (older SM without /operations), the synchronous
    202 (state="published") is the terminal result: no background poll task is
    launched and the operation is marked ``completed`` immediately.
    """
    init_db()
    monkeypatch.setattr(settings, "sm_polling_enabled", False)
    fake_sm.request_ids["associate"] = "sm-req-associate"
    poll_calls: list = []
    _capture_polls(monkeypatch, poll_calls)

    with TestClient(app) as client:
        op = _create_and_get_operation(client, "no-poll-key")

    assert op["status"] == "completed"
    assert op["smRequestIds"] == ["sm-req-associate"]
    assert op["smSliceId"] == "placeholder-slice-1"
    assert poll_calls == []


def test_create_with_polling_tracks_the_associate(sqlite_db, fake_sm, monkeypatch) -> None:
    """With SM polling enabled (the default), the operation stays in
    ``sm_provisioning`` and one poll task follows the associate request.
    """
    init_db()
    monkeypatch.setattr(settings, "sm_polling_enabled", True)
    fake_sm.request_ids["associate"] = "sm-req-associate"
    poll_calls: list = []
    _capture_polls(monkeypatch, poll_calls)

    with TestClient(app) as client:
        op = _create_and_get_operation(client, "poll-key")

    assert op["status"] == "sm_provisioning"
    assert op["smRequestIds"] == ["sm-req-associate"]
    assert len(poll_calls) == 1
    assert poll_calls[0]["sm_request_id"] == "sm-req-associate"
    assert poll_calls[0]["operation_id"] == op["operationId"]
