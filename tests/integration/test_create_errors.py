"""Create errors through the single create flow of the core.

A refusal writes nothing; a failed SM call records a failed operation and no
session, and answers 502, or 503 while the circuit breaker is open.
"""

from __future__ import annotations

import copy

from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.db.schema import init_db
from app.impl.sm_client import SliceManagerClient
from app.main import app
from app.resilience.circuit_breaker import CircuitBreakerOpen
from app.store.subscription_store import store

N5 = "/npcf-policyauthorization/v1/app-sessions"
N5_BODY = {
    "ascReqData": {
        "notifUri": "http://nef.example/pa-callback",
        "suppFeat": "0",
        "ueIpv4": "10.0.0.1",
        "medComponents": {"1": {"medCompN": 1, "qosReference": "qos_ref_1"}},
    }
}
AS_QOS = "/3gpp-as-session-with-qos/v1/myApp/subscriptions"
AS_QOS_BODY = {
    "notificationDestination": "http://localhost:9999/callback",
    "qosReference": "qos_ref_1",
    "ueIpv4Addr": "10.0.0.1",
}


def _operations() -> list:
    return [dict(r) for r in get_connection().execute("SELECT status, error FROM operations")]


def _failing_associate(exc: Exception):
    async def _associate(self, ue_id, payload):
        raise exc

    return _associate


def test_refused_creates_write_nothing(sqlite_db, fake_sm) -> None:
    init_db()
    unknown_profile = copy.deepcopy(N5_BODY)
    unknown_profile["ascReqData"]["medComponents"]["1"]["qosReference"] = "no_such_profile"
    unknown_ue = copy.deepcopy(N5_BODY)
    unknown_ue["ascReqData"]["ueIpv4"] = "10.9.9.9"

    with TestClient(app) as client:
        assert client.post(N5, json=unknown_profile).status_code == 403
        assert client.post(N5, json=unknown_ue).status_code == 404
        assert client.post(AS_QOS, json={**AS_QOS_BODY, "qosReference": "no_such_profile"}).status_code == 400

    assert _operations() == []
    assert store.count == 0
    assert fake_sm.associate == []


def test_second_session_for_a_ue_writes_nothing_more(sqlite_db, fake_sm) -> None:
    init_db()

    with TestClient(app) as client:
        assert client.post(N5, json=N5_BODY).status_code == 201
        assert client.post(AS_QOS, json=AS_QOS_BODY).status_code == 403

    assert len(_operations()) == 1
    assert store.count == 1


def test_sm_failure_answers_502_and_records_a_failed_operation(sqlite_db, fake_sm, monkeypatch) -> None:
    init_db()
    monkeypatch.setattr(
        SliceManagerClient, "associate_slice", _failing_associate(RuntimeError("SM down"))
    )

    with TestClient(app) as client:
        response = client.post(N5, json=N5_BODY)

    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/problem+json")
    (operation,) = _operations()
    assert operation["status"] == "failed"
    assert "SM associate_slice failed: SM down" in operation["error"]
    assert store.count == 0


def test_open_circuit_answers_503(sqlite_db, fake_sm, monkeypatch) -> None:
    init_db()
    monkeypatch.setattr(
        SliceManagerClient,
        "associate_slice",
        _failing_associate(CircuitBreakerOpen("slice-manager")),
    )

    with TestClient(app) as client:
        assert client.post(AS_QOS, json=AS_QOS_BODY).status_code == 503
        # The idempotency reservation was released: the same request may try again
        assert client.post(AS_QOS, json=AS_QOS_BODY).status_code == 503

    assert [op["status"] for op in _operations()] == ["failed", "failed"]
    assert store.count == 0
