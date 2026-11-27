"""29.122 under model B (docs/adr/0006): profile → slice, one session per UE,
and tracked QoS changes."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.db.schema import init_db
from app.main import app
from app.store.repositories import OperationRepository
from app.store.subscription_store import store

BASE = "/3gpp-as-session-with-qos/v1/myApp/subscriptions"

CREATE_BODY = {
    "notificationDestination": "http://localhost:9999/callback",
    "qosReference": "qos_ref_1",
    "ueIpv4Addr": "10.0.0.2",
    "dnn": "internet",
    "snssai": {"sst": 1, "sd": "000001"},
}


def _create_subscription(client: TestClient) -> str:
    response = client.post(BASE, json=CREATE_BODY, headers={"Idempotency-Key": "create-update-flow"})
    assert response.status_code == 201
    return response.json()["self"].rsplit("/", 1)[-1]


def _capture_polls(monkeypatch) -> list:
    polls: list = []

    async def _fake_poll(*args, **kwargs):
        polls.append(kwargs)

    monkeypatch.setattr("app.impl.session_core.poll_sm_request", _fake_poll)
    return polls


# ── Create: the profile selects a pre-provisioned slice ───────────────────

def test_create_associates_the_ue_with_the_profile_slice(sqlite_db, fake_sm) -> None:
    init_db()

    with TestClient(app) as client:
        sub_id = _create_subscription(client)

    ((imsi, payload),) = fake_sm.associate
    assert imsi == "999080100001152"
    assert payload["slice_id"] == "placeholder-slice-1"
    assert payload["snssai"] == "1-000001"
    assert payload["dnn"] == "internet"
    assert store.get("myApp", sub_id).sm_slice_id == "placeholder-slice-1"


def test_create_without_qos_reference_is_rejected(sqlite_db, fake_sm) -> None:
    init_db()
    body = {k: v for k, v in CREATE_BODY.items() if k != "qosReference"}

    with TestClient(app) as client:
        response = client.post(BASE, json=body)

    assert response.status_code == 400
    assert "qosReference is required" in response.json()["detail"]
    assert fake_sm.associate == []


def test_create_with_snssai_or_dnn_not_matching_the_profile_is_rejected(sqlite_db, fake_sm) -> None:
    init_db()

    with TestClient(app) as client:
        for override in ({"snssai": {"sst": 2}}, {"snssai": {"sst": 1, "sd": "000002"}}, {"dnn": "ims"}):
            response = client.post(BASE, json={**CREATE_BODY, **override})
            assert response.status_code == 400, override
            assert "does not match" in response.json()["detail"]
        # An SST without SD still matches the profile's slice
        ok = client.post(BASE, json={**CREATE_BODY, "snssai": {"sst": 1}})
        assert ok.status_code == 201

    assert len(fake_sm.associate) == 1


def test_second_session_for_the_same_ue_is_rejected(sqlite_db, fake_sm) -> None:
    init_db()

    with TestClient(app) as client:
        first = client.post(BASE, json=CREATE_BODY)
        assert first.status_code == 201
        # Another AF, same UE
        second = client.post(
            "/3gpp-as-session-with-qos/v1/otherApp/subscriptions",
            json={**CREATE_BODY, "qosReference": "qos_ref_2"},
        )
        assert second.status_code == 403
        assert "already has an active QoS session" in second.json()["detail"]

        # Once the first session is gone, the UE can get a new one
        sub_id = first.json()["self"].rsplit("/", 1)[-1]
        assert client.delete(f"{BASE}/{sub_id}").status_code == 204
        third = client.post(BASE, json=CREATE_BODY)
        assert third.status_code == 201

    assert len(fake_sm.associate) == 2


# ── Change: move between slices, tracked ──────────────────────────────────

def test_put_moves_the_ue_to_the_new_profile_slice(sqlite_db, fake_sm) -> None:
    init_db()

    with TestClient(app) as client:
        sub_id = _create_subscription(client)
        response = client.put(
            f"{BASE}/{sub_id}",
            json={**CREATE_BODY, "qosReference": "qos_ref_2",
                  "notificationDestination": "http://localhost:9999/callback-v2"},
        )

    assert response.status_code == 200
    ((imsi, change_payload),) = fake_sm.change
    assert imsi == "999080100001152"
    assert change_payload == {
        "slice_id": "placeholder-slice-2",
        "snssai": "1-000001",
        "dnn": "internet",
    }
    assert store.get("myApp", sub_id).sm_slice_id == "placeholder-slice-2"


def test_change_to_the_same_slice_does_not_call_the_sm(sqlite_db, fake_sm) -> None:
    init_db()

    with TestClient(app) as client:
        sub_id = _create_subscription(client)
        put = client.put(f"{BASE}/{sub_id}", json=CREATE_BODY)
        patch = client.patch(f"{BASE}/{sub_id}", json={"tscQosReq": {"req5Gsdelay": 7}})

    assert put.status_code == 200 and patch.status_code == 200
    assert fake_sm.change == []


def test_patch_without_qos_fields_does_not_call_change_slice(sqlite_db, fake_sm) -> None:
    init_db()

    with TestClient(app) as client:
        sub_id = _create_subscription(client)
        response = client.patch(
            f"{BASE}/{sub_id}", json={"notificationDestination": "http://localhost:9999/callback-v3"}
        )

    assert response.status_code == 200
    assert fake_sm.change == []


def test_patch_with_qos_reference_calls_change_slice(sqlite_db, fake_sm) -> None:
    init_db()

    with TestClient(app) as client:
        sub_id = _create_subscription(client)
        response = client.patch(f"{BASE}/{sub_id}", json={"qosReference": "qos_ref_3"})

    assert response.status_code == 200
    ((_, payload),) = fake_sm.change
    assert payload["slice_id"] == "placeholder-slice-3"
    assert store.get("myApp", sub_id).sm_slice_id == "placeholder-slice-3"


def test_patch_to_an_unknown_profile_is_rejected_and_keeps_the_slice(sqlite_db, fake_sm) -> None:
    init_db()

    with TestClient(app) as client:
        sub_id = _create_subscription(client)
        response = client.patch(f"{BASE}/{sub_id}", json={"qosReference": "no_such_profile"})

    assert response.status_code == 400
    assert fake_sm.change == []
    assert store.get("myApp", sub_id).sm_slice_id == "placeholder-slice-1"


def test_rejected_move_fails_the_request_and_stores_nothing(sqlite_db, fake_sm, monkeypatch) -> None:
    init_db()
    from app.impl.sm_client import SliceManagerClient

    async def _failing_change(self, ue_id, payload):
        raise RuntimeError("SM unavailable")

    with TestClient(app) as client:
        sub_id = _create_subscription(client)
        monkeypatch.setattr(SliceManagerClient, "change_slice", _failing_change)
        response = client.patch(f"{BASE}/{sub_id}", json={"qosReference": "qos_ref_2"})
        stored = client.get(f"{BASE}/{sub_id}").json()

    assert response.status_code == 502
    assert stored["qosReference"] == "qos_ref_1"
    assert store.get("myApp", sub_id).sm_slice_id == "placeholder-slice-1"


def test_accepted_move_is_tracked_with_the_previous_slice(sqlite_db, fake_sm, monkeypatch) -> None:
    init_db()
    monkeypatch.setattr(settings, "sm_polling_enabled", True)
    fake_sm.request_ids["change"] = "sm-req-change"

    with TestClient(app) as client:
        sub_id = _create_subscription(client)
        polls = _capture_polls(monkeypatch)
        response = client.patch(f"{BASE}/{sub_id}", json={"qosReference": "qos_ref_2"})

    assert response.status_code == 200
    (poll,) = polls
    assert poll["sm_request_id"] == "sm-req-change"
    operation = OperationRepository().get(poll["operation_id"])
    assert operation["status"] == "sm_provisioning"
    assert operation["subscription_id"] == sub_id
    assert operation["sm_slice_id"] == "placeholder-slice-2"
    assert operation["previous_sm_slice_id"] == "placeholder-slice-1"
