"""API-level tests for the issues fixed after the architecture review (I1, I4, I6–I8)."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.db.schema import init_db
from app.impl.sm_client import SliceManagerClient
from app.main import app


BASE = "/3gpp-as-session-with-qos/v1/myApp/subscriptions"

CREATE_BODY = {
    "notificationDestination": "http://localhost:9999/callback",
    "qosReference": "qos_ref_1",
    "ueIpv4Addr": "10.0.0.1",
    "dnn": "internet",
    "exterAppId": "camera-app",
}

MERGE_PATCH = {"Content-Type": "application/merge-patch+json"}


def _sub_id(response) -> str:
    return response.json()["self"].rsplit("/", 1)[-1]


# ── I1: idempotency is released when the outcome no longer stands ─────────

def test_identical_create_after_delete_creates_a_new_subscription(sqlite_db, fake_sm, monkeypatch) -> None:
    """A camera that reconnects with the same request must get a new session."""
    init_db()

    with TestClient(app) as client:
        for headers in ({"Idempotency-Key": "camera-1"}, {}):
            first = client.post(BASE, json=CREATE_BODY, headers=headers)
            assert first.status_code == 201
            assert client.delete(f"{BASE}/{_sub_id(first)}").status_code == 204

            second = client.post(BASE, json=CREATE_BODY, headers=headers)
            assert second.status_code == 201
            assert _sub_id(second) != _sub_id(first)
            assert client.delete(f"{BASE}/{_sub_id(second)}").status_code == 204


def test_failed_create_can_be_retried(sqlite_db, fake_sm, monkeypatch) -> None:
    init_db()
    attempts = {"n": 0}

    async def _flaky_associate(self, ue_id, payload):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("SM unavailable")
        return ""

    monkeypatch.setattr(SliceManagerClient, "associate_slice", _flaky_associate)

    with TestClient(app) as client:
        failed = client.post(BASE, json=CREATE_BODY, headers={"Idempotency-Key": "retry-me"})
        assert failed.status_code == 502

        retried = client.post(BASE, json=CREATE_BODY, headers={"Idempotency-Key": "retry-me"})
        assert retried.status_code == 201


# ── I4: PUT cannot move a subscription to another UE ──────────────────────

def test_put_cannot_change_the_ue(sqlite_db, fake_sm, monkeypatch) -> None:
    init_db()

    with TestClient(app) as client:
        sub_id = _sub_id(client.post(BASE, json=CREATE_BODY))

        response = client.put(f"{BASE}/{sub_id}", json={**CREATE_BODY, "ueIpv4Addr": "10.0.0.2"})
        assert response.status_code == 400
        assert "cannot be changed" in response.json()["detail"]
        assert fake_sm.change == []

        stored = client.get(f"{BASE}/{sub_id}").json()
        assert stored["ueIpv4Addr"] == "10.0.0.1"


# ── I6: errors are 3GPP ProblemDetails ────────────────────────────────────

def test_errors_are_problem_details(sqlite_db, fake_sm, monkeypatch) -> None:
    init_db()

    with TestClient(app) as client:
        response = client.get(f"{BASE}/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json() == {
        "title": "Not Found",
        "status": 404,
        "detail": "Subscription not found",
    }


def test_validation_errors_are_400_with_invalid_params(sqlite_db, fake_sm, monkeypatch) -> None:
    init_db()

    with TestClient(app) as client:
        response = client.post(BASE, json={"qosReference": "qos_ref_1"})

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 400
    assert {"param": "notificationDestination", "reason": "Field required"} in body["invalidParams"]


# ── I7: ip-addrs as in TS 29.122 ──────────────────────────────────────────

def test_list_filters_by_ip_addrs(sqlite_db, fake_sm, monkeypatch) -> None:
    init_db()

    with TestClient(app) as client:
        client.post(BASE, json=CREATE_BODY)
        client.post(BASE, json={**CREATE_BODY, "ueIpv4Addr": "10.0.0.2"})

        def listed(ip_addrs: str) -> list:
            response = client.get(BASE, params={"ip-addrs": ip_addrs})
            assert response.status_code == 200
            return sorted(s["ueIpv4Addr"] for s in response.json())

        assert listed(json.dumps([{"ipv4Addr": "10.0.0.2"}])) == ["10.0.0.2"]
        assert listed("10.0.0.1") == ["10.0.0.1"]
        assert listed("10.0.0.1,10.0.0.2") == ["10.0.0.1", "10.0.0.2"]

        bad = client.get(BASE, params={"ip-addrs": json.dumps({"ipv4Addr": "10.0.0.1"})})
        assert bad.status_code == 400


# ── I8: merge-patch null removes a member ─────────────────────────────────

def test_patch_null_removes_a_member(sqlite_db, fake_sm, monkeypatch) -> None:
    init_db()

    with TestClient(app) as client:
        sub_id = _sub_id(client.post(BASE, json=CREATE_BODY))

        response = client.patch(
            f"{BASE}/{sub_id}", content=json.dumps({"exterAppId": None}), headers=MERGE_PATCH
        )
        assert response.status_code == 200
        assert "exterAppId" not in response.json()
        assert "exterAppId" not in client.get(f"{BASE}/{sub_id}").json()
        # Not a QoS change: the SM is not involved
        assert fake_sm.change == []


def test_patch_cannot_remove_a_required_member(sqlite_db, fake_sm, monkeypatch) -> None:
    init_db()

    with TestClient(app) as client:
        sub_id = _sub_id(client.post(BASE, json=CREATE_BODY))

        response = client.patch(
            f"{BASE}/{sub_id}",
            content=json.dumps({"notificationDestination": None}),
            headers=MERGE_PATCH,
        )
        assert response.status_code == 400
        assert client.get(f"{BASE}/{sub_id}").json()["notificationDestination"]


def test_openapi_documents_problem_details() -> None:
    schema = app.openapi()

    for path_item in schema["paths"].values():
        for operation in path_item.values():
            assert "422" not in operation["responses"]
    assert "ProblemDetails" in schema["components"]["schemas"]
    assert "HTTPValidationError" not in schema["components"]["schemas"]
