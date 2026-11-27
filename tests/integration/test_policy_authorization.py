"""N5 (TS 29.514 Npcf_PolicyAuthorization) endpoints: the NEF calls the translator as its PCF."""

from __future__ import annotations

import copy

from fastapi.testclient import TestClient

from app.db.schema import init_db
from app.main import app

BASE = "/npcf-policyauthorization/v1/app-sessions"
MERGE_PATCH = {"Content-Type": "application/merge-patch+json"}

CREATE_BODY = {
    "ascReqData": {
        "notifUri": "http://nef.example/pa-callback",
        "suppFeat": "0",
        "ueIpv4": "10.0.0.1",
        "dnn": "internet",
        "afAppId": "camera-app",
        "medComponents": {
            "1": {
                "medCompN": 1,
                "medType": "VIDEO",
                "qosReference": "qos_ref_1",
                "mirBwDl": "10 Mbps",
                "marBwUl": "20 Mbps",
            }
        },
        "evSubsc": {
            "events": [{"event": "SUCCESSFUL_RESOURCES_ALLOCATION"}],
            "notifUri": "http://nef.example/pa-events",
        },
    }
}


def _session_uri(response) -> str:
    return response.headers["Location"]


def test_create_returns_201_with_location_and_starts_the_session(sqlite_db, fake_sm) -> None:
    init_db()

    with TestClient(app) as client:
        response = client.post(BASE, json=CREATE_BODY)

    assert response.status_code == 201
    location = _session_uri(response)
    assert location.startswith("http://testserver/npcf-policyauthorization/v1/app-sessions/")
    # The context is echoed back as received, unknown members included
    assert response.json()["ascReqData"]["afAppId"] == "camera-app"

    ((imsi, payload),) = fake_sm.associate
    assert imsi == "999080100001151"
    assert payload["slice_id"] == "placeholder-slice-1"  # qos_ref_1
    assert payload["snssai"] == "1-000001" and payload["dnn"] == "internet"


def test_create_rejects_unknown_qos_reference_with_403(sqlite_db, fake_sm) -> None:
    init_db()
    body = copy.deepcopy(CREATE_BODY)
    body["ascReqData"]["medComponents"]["1"]["qosReference"] = "no_such_profile"

    with TestClient(app) as client:
        response = client.post(BASE, json=body)

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")
    assert fake_sm.associate == []


def test_create_rejects_conflicting_qos_references_with_403(sqlite_db, fake_sm) -> None:
    init_db()
    body = copy.deepcopy(CREATE_BODY)
    body["ascReqData"]["medComponents"]["2"] = {"medCompN": 2, "qosReference": "qos_ref_2"}

    with TestClient(app) as client:
        assert client.post(BASE, json=body).status_code == 403


def test_create_rejects_unknown_ue_with_404(sqlite_db, fake_sm) -> None:
    init_db()
    body = copy.deepcopy(CREATE_BODY)
    body["ascReqData"]["ueIpv4"] = "192.0.2.99"

    with TestClient(app) as client:
        assert client.post(BASE, json=body).status_code == 404


def test_create_requires_notif_uri_supp_feat_and_a_ue_address(sqlite_db, fake_sm) -> None:
    init_db()

    with TestClient(app) as client:
        for member in ("notifUri", "suppFeat", "ueIpv4"):
            body = copy.deepcopy(CREATE_BODY)
            del body["ascReqData"][member]
            response = client.post(BASE, json=body)
            assert response.status_code == 400, member


def test_patch_qos_reference_changes_the_session(sqlite_db, fake_sm) -> None:
    init_db()

    with TestClient(app) as client:
        uri = _session_uri(client.post(BASE, json=CREATE_BODY))
        app_session_id = uri.rsplit("/", 1)[-1]
        patch = {"ascReqData": {"medComponents": {"1": {"medCompN": 1, "qosReference": "qos_ref_2"}}}}
        response = client.patch(f"{BASE}/{app_session_id}", json=patch, headers=MERGE_PATCH)

    assert response.status_code == 200
    component = response.json()["ascReqData"]["medComponents"]["1"]
    assert component["qosReference"] == "qos_ref_2"
    # Members the patch did not touch survive the merge
    assert component["mirBwDl"] == "10 Mbps"
    ((ue_id, change_payload),) = fake_sm.change
    assert change_payload["slice_id"] == "placeholder-slice-2"


def test_patch_without_media_components_does_not_call_the_sm(sqlite_db, fake_sm) -> None:
    init_db()

    with TestClient(app) as client:
        app_session_id = _session_uri(client.post(BASE, json=CREATE_BODY)).rsplit("/", 1)[-1]
        response = client.patch(
            f"{BASE}/{app_session_id}",
            json={"ascReqData": {"afAppId": "camera-app-2"}},
            headers=MERGE_PATCH,
        )

    assert response.status_code == 200
    assert response.json()["ascReqData"]["afAppId"] == "camera-app-2"
    assert fake_sm.change == []


def test_delete_stops_the_session_and_forgets_it(sqlite_db, fake_sm) -> None:
    init_db()

    with TestClient(app) as client:
        app_session_id = _session_uri(client.post(BASE, json=CREATE_BODY)).rsplit("/", 1)[-1]
        first = client.post(f"{BASE}/{app_session_id}/delete")
        second = client.post(f"{BASE}/{app_session_id}/delete")

    assert first.status_code == 204
    assert second.status_code == 404
    # Model B: only the UE association goes; the slice belongs to the SM
    assert fake_sm.dissociate == [("999080100001151", "placeholder-slice-1")]


def test_methods_outside_the_minimal_scope_answer_405(sqlite_db, fake_sm) -> None:
    init_db()

    with TestClient(app) as client:
        app_session_id = _session_uri(client.post(BASE, json=CREATE_BODY)).rsplit("/", 1)[-1]
        assert client.get(f"{BASE}/{app_session_id}").status_code == 405
        assert client.put(f"{BASE}/{app_session_id}", json={}).status_code == 405


def test_create_without_qos_reference_is_rejected_with_403(sqlite_db, fake_sm) -> None:
    init_db()
    body = copy.deepcopy(CREATE_BODY)
    del body["ascReqData"]["medComponents"]["1"]["qosReference"]

    with TestClient(app) as client:
        response = client.post(BASE, json=body)

    assert response.status_code == 403
    assert fake_sm.associate == []


def test_create_with_slice_info_or_dnn_not_matching_the_profile_is_rejected(sqlite_db, fake_sm) -> None:
    init_db()

    with TestClient(app) as client:
        for member, value in (("sliceInfo", {"sst": 2, "sd": "000002"}), ("dnn", "ims")):
            body = copy.deepcopy(CREATE_BODY)
            body["ascReqData"][member] = value
            response = client.post(BASE, json=body)
            assert response.status_code == 403, member
            assert "does not match" in response.json()["detail"]
        body = copy.deepcopy(CREATE_BODY)
        body["ascReqData"]["sliceInfo"] = {"sst": 1, "sd": "000001"}
        assert client.post(BASE, json=body).status_code == 201


def test_second_session_for_the_same_ue_is_rejected_with_403(sqlite_db, fake_sm) -> None:
    """One session per UE across both APIs: N5 lists no 409 for the create."""
    init_db()

    with TestClient(app) as client:
        assert client.post(BASE, json=CREATE_BODY).status_code == 201
        assert client.post(BASE, json=CREATE_BODY).status_code == 403
        # The same UE through TS 29.122
        response = client.post(
            "/3gpp-as-session-with-qos/v1/myApp/subscriptions",
            json={
                "notificationDestination": "http://localhost:9999/callback",
                "qosReference": "qos_ref_1",
                "ueIpv4Addr": "10.0.0.1",
            },
        )
        assert response.status_code == 403

    assert len(fake_sm.associate) == 1
