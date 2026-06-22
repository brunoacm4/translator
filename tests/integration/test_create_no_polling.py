from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.schema import init_db
from app.impl.sm_client import SliceManagerClient
from app.main import app


REQUEST_BODY = {
    "notificationDestination": "http://localhost:9999/callback",
    "qosReference": "qos_ref_1",
    "ueIpv4Addr": "10.0.0.1",
    "dnn": "internet",
}


def test_create_without_polling_marks_operation_completed(sqlite_db, monkeypatch) -> None:
    """With SM polling disabled (the default), the synchronous 202 from the SM
    (state="published") is the terminal result: no background poll task is
    launched and the operation is marked ``completed`` immediately.

    This guards the behaviour while the Slice Manager has no working
    ``GET /operations/{request_id}`` endpoint (see settings.sm_polling_enabled).
    """
    init_db()

    async def _create(self, payload):
        return ""

    async def _associate(self, ue_id, payload):
        # Non-empty request_id: would normally trigger polling if enabled
        return "sm-req-123"

    poll_calls = {"n": 0}

    async def _fake_poll(*args, **kwargs):
        poll_calls["n"] += 1

    monkeypatch.setattr(SliceManagerClient, "create_slice", _create)
    monkeypatch.setattr(SliceManagerClient, "associate_slice", _associate)
    # Patch the symbol as imported into the service module
    monkeypatch.setattr("app.impl.translator_service.poll_sm_request", _fake_poll)

    with TestClient(app) as client:
        r1 = client.post(
            "/3gpp-as-session-with-qos/v1/myApp/subscriptions",
            json=REQUEST_BODY,
            headers={"Idempotency-Key": "no-poll-key"},
        )
        assert r1.status_code == 201

        # A duplicate request echoes the original operationId so we can inspect it
        r2 = client.post(
            "/3gpp-as-session-with-qos/v1/myApp/subscriptions",
            json=REQUEST_BODY,
            headers={"Idempotency-Key": "no-poll-key"},
        )
        assert r2.status_code == 202
        op_id = r2.json()["operationId"]

        op = client.get(f"/3gpp-as-session-with-qos/v1/operations/{op_id}")
        assert op.status_code == 200
        assert op.json()["status"] == "completed"

    # Polling must NOT be launched while SM polling is disabled
    assert poll_calls["n"] == 0
