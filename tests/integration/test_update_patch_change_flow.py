from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.schema import init_db
from app.impl.sm_client import SliceManagerClient
from app.main import app


CREATE_BODY = {
    "notificationDestination": "http://localhost:9999/callback",
    "qosReference": "qos_ref_1",
    "ueIpv4Addr": "10.0.0.2",
    "dnn": "internet",
    "snssai": {"sst": 1, "sd": "000001"},
}


def _create_subscription(client: TestClient) -> str:
    response = client.post(
        "/3gpp-as-session-with-qos/v1/myApp/subscriptions",
        json=CREATE_BODY,
        headers={"Idempotency-Key": "create-update-flow"},
    )
    assert response.status_code == 201
    self_url = response.json()["self"]
    return self_url.rsplit("/", 1)[-1]


def test_put_calls_change_slice_with_sm_required_fields(sqlite_db, monkeypatch) -> None:
    init_db()

    calls = {
        "create_payload": None,
        "associate_payload": None,
        "change_payload": None,
    }

    async def _create(self, payload):
        calls["create_payload"] = payload
        return ""

    async def _associate(self, ue_id, payload):
        calls["associate_payload"] = payload
        return ""

    async def _change(self, ue_id, payload):
        calls["change_payload"] = payload
        return ""

    monkeypatch.setattr(SliceManagerClient, "create_slice", _create)
    monkeypatch.setattr(SliceManagerClient, "associate_slice", _associate)
    monkeypatch.setattr(SliceManagerClient, "change_slice", _change)

    with TestClient(app) as client:
        subscription_id = _create_subscription(client)

        update_body = {
            "notificationDestination": "http://localhost:9999/callback-v2",
            "qosReference": "qos_ref_2",
            "ueIpv4Addr": "10.0.0.2",
            "dnn": "internet",
            "snssai": {"sst": 1, "sd": "000001"},
        }
        response = client.put(
            f"/3gpp-as-session-with-qos/v1/myApp/subscriptions/{subscription_id}",
            json=update_body,
        )

    assert response.status_code == 200
    assert calls["create_payload"] is not None
    assert calls["associate_payload"] is not None
    assert calls["change_payload"] is not None

    change_payload = calls["change_payload"]
    assert change_payload["slice_id"] == calls["create_payload"]["slice_id"]
    assert change_payload["dnn"] == "internet"
    assert change_payload["snssai"] == "1-000001"


def test_patch_without_qos_fields_does_not_call_change_slice(sqlite_db, monkeypatch) -> None:
    init_db()

    calls = {"change": 0}

    async def _create(self, payload):
        return ""

    async def _associate(self, ue_id, payload):
        return ""

    async def _change(self, ue_id, payload):
        calls["change"] += 1
        return ""

    monkeypatch.setattr(SliceManagerClient, "create_slice", _create)
    monkeypatch.setattr(SliceManagerClient, "associate_slice", _associate)
    monkeypatch.setattr(SliceManagerClient, "change_slice", _change)

    with TestClient(app) as client:
        subscription_id = _create_subscription(client)

        patch_body = {"notificationDestination": "http://localhost:9999/callback-v3"}
        response = client.patch(
            f"/3gpp-as-session-with-qos/v1/myApp/subscriptions/{subscription_id}",
            json=patch_body,
        )

    assert response.status_code == 200
    assert calls["change"] == 0


def test_patch_with_qos_reference_calls_change_slice(sqlite_db, monkeypatch) -> None:
    init_db()

    calls = {"change_payload": None}

    async def _create(self, payload):
        return ""

    async def _associate(self, ue_id, payload):
        return ""

    async def _change(self, ue_id, payload):
        calls["change_payload"] = payload
        return ""

    monkeypatch.setattr(SliceManagerClient, "create_slice", _create)
    monkeypatch.setattr(SliceManagerClient, "associate_slice", _associate)
    monkeypatch.setattr(SliceManagerClient, "change_slice", _change)

    with TestClient(app) as client:
        subscription_id = _create_subscription(client)

        patch_body = {"qosReference": "qos_ref_3"}
        response = client.patch(
            f"/3gpp-as-session-with-qos/v1/myApp/subscriptions/{subscription_id}",
            json=patch_body,
        )

    assert response.status_code == 200
    assert calls["change_payload"] is not None
    assert set(calls["change_payload"].keys()) == {"slice_id", "dnn", "snssai"}
