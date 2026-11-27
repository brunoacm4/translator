from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.schema import init_db
from app.main import app


REQUEST_BODY = {
    "notificationDestination": "http://localhost:9999/callback",
    "qosReference": "qos_ref_1",
    "ueIpv4Addr": "10.0.0.1",
    "dnn": "internet",
}


def test_duplicate_create_with_same_idempotency_key_returns_202(sqlite_db, fake_sm) -> None:
    init_db()

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

    assert len(fake_sm.associate) == 1


def test_duplicate_create_without_header_uses_fingerprint(sqlite_db, fake_sm) -> None:
    init_db()

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

    assert len(fake_sm.associate) == 1
