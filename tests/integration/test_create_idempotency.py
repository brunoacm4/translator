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


def test_duplicate_create_with_same_idempotency_key_returns_202(sqlite_db, monkeypatch) -> None:
    init_db()

    calls = {"create": 0, "associate": 0}

    async def _create(self, payload):
        calls["create"] += 1
        return ""

    async def _associate(self, ue_id, payload):
        calls["associate"] += 1
        return ""

    monkeypatch.setattr(SliceManagerClient, "create_slice", _create)
    monkeypatch.setattr(SliceManagerClient, "associate_slice", _associate)

    with TestClient(app) as client:
        r1 = client.post(
            "/3gpp-as-session-with-qos/v1/myApp/subscriptions",
            json=REQUEST_BODY,
            headers={"Idempotency-Key": "same-key"},
        )
        assert r1.status_code == 201

        r2 = client.post(
            "/3gpp-as-session-with-qos/v1/myApp/subscriptions",
            json=REQUEST_BODY,
            headers={"Idempotency-Key": "same-key"},
        )
        assert r2.status_code == 202
        assert "operationId" in r2.json()

    assert calls["create"] == 1
    assert calls["associate"] == 1


def test_duplicate_create_without_header_uses_fingerprint(sqlite_db, monkeypatch) -> None:
    init_db()

    calls = {"create": 0, "associate": 0}

    async def _create(self, payload):
        calls["create"] += 1
        return ""

    async def _associate(self, ue_id, payload):
        calls["associate"] += 1
        return ""

    monkeypatch.setattr(SliceManagerClient, "create_slice", _create)
    monkeypatch.setattr(SliceManagerClient, "associate_slice", _associate)

    with TestClient(app) as client:
        r1 = client.post(
            "/3gpp-as-session-with-qos/v1/myApp/subscriptions",
            json=REQUEST_BODY,
        )
        assert r1.status_code == 201

        r2 = client.post(
            "/3gpp-as-session-with-qos/v1/myApp/subscriptions",
            json=REQUEST_BODY,
        )
        assert r2.status_code == 202

    assert calls["create"] == 1
    assert calls["associate"] == 1
