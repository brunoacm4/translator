"""Rejected requests are logged with their reason, never with their body (CONSIDERATIONS §4.14)."""

from __future__ import annotations

import copy
import logging
from typing import Iterator, List

import pytest
from fastapi.testclient import TestClient

from app.db.schema import init_db
from app.main import app

BASE = "/npcf-policyauthorization/v1/app-sessions"

CREATE_BODY = {
    "ascReqData": {
        "notifUri": "http://nef.example/pa-callback",
        "suppFeat": "0",
        "ueIpv4": "10.0.0.1",
        "dnn": "internet",
        "medComponents": {"1": {"medCompN": 1, "medType": "VIDEO", "qosReference": "qos_ref_1"}},
    }
}


class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: List[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def rejections() -> Iterator[_Collector]:
    """Records of the rejection logger.

    The handler sits on the logger itself, so it survives the app replacing the
    root handlers when it starts.
    """
    collector = _Collector()
    log = logging.getLogger("app.apis.problem_details")
    log.addHandler(collector)
    try:
        yield collector
    finally:
        log.removeHandler(collector)


def _only_message(collector: _Collector) -> str:
    (record,) = collector.records
    assert record.levelno == logging.WARNING
    return record.getMessage()


def test_unknown_qos_reference_is_logged_with_reason_and_without_body(
    sqlite_db, fake_sm, rejections
) -> None:
    init_db()
    body = copy.deepcopy(CREATE_BODY)
    body["ascReqData"]["medComponents"]["1"]["qosReference"] = "no_such_profile"

    with TestClient(app) as client:
        assert client.post(BASE, json=body).status_code == 403

    message = _only_message(rejections)
    assert "method=POST" in message and f"path={BASE}" in message
    assert "status=403" in message and "no_such_profile" in message
    assert "10.0.0.1" not in message and "nef.example" not in message


def test_unknown_ue_is_logged_without_its_address(sqlite_db, fake_sm, rejections) -> None:
    init_db()
    body = copy.deepcopy(CREATE_BODY)
    body["ascReqData"]["ueIpv4"] = "10.9.9.9"

    with TestClient(app) as client:
        response = client.post(BASE, json=body)

    assert response.status_code == 404
    assert "10.9.9.9" not in response.json()["detail"]
    message = _only_message(rejections)
    assert "status=404" in message and "subscriber map" in message
    assert "10.9.9.9" not in message


def test_invalid_request_is_logged_with_parameter_names_only(
    sqlite_db, fake_sm, rejections
) -> None:
    init_db()
    body = copy.deepcopy(CREATE_BODY)
    del body["ascReqData"]["suppFeat"]

    with TestClient(app) as client:
        assert client.post(BASE, json=body).status_code == 400

    message = _only_message(rejections)
    assert "status=400" in message and "ascReqData.suppFeat" in message
    assert "10.0.0.1" not in message and "nef.example" not in message


def test_request_to_an_unknown_path_is_logged(sqlite_db, fake_sm, rejections) -> None:
    init_db()

    with TestClient(app) as client:
        assert client.post("/npcf-policyauthorization/v1/no-such-resource", json={}).status_code == 404

    message = _only_message(rejections)
    assert "path=/npcf-policyauthorization/v1/no-such-resource" in message and "status=404" in message


def test_accepted_request_logs_no_rejection(sqlite_db, fake_sm, rejections) -> None:
    init_db()

    with TestClient(app) as client:
        assert client.post(BASE, json=CREATE_BODY).status_code == 201

    assert rejections.records == []
