"""N5 request mapping and termination request building (no HTTP, no SM)."""

from __future__ import annotations

import pytest

from app.impl.notifier import build_termination_request
from app.impl.policy_authorization_service import PolicyAuthorizationService
from app.models.pcf.app_session import AppSessionContextReqData

RESOURCE_URI = "http://translator:8081/npcf-policyauthorization/v1/app-sessions/abc"


def _req(**overrides) -> AppSessionContextReqData:
    data = {"notifUri": "http://nef/cb", "suppFeat": "0", "ueIpv4": "10.0.0.1"}
    data.update(overrides)
    return AppSessionContextReqData.model_validate(data)


def test_intent_takes_qos_and_bitrates_from_the_media_component() -> None:
    req = _req(
        dnn="internet",
        sliceInfo={"sst": 2, "sd": "000002"},
        medComponents={"1": {"medCompN": 1, "qosReference": "qos_ref_1",
                             "mirBwUl": "1 Mbps", "marBwDl": "1 Gbps"}},
    )
    intent = PolicyAuthorizationService._intent(req)
    assert intent.qos_reference == "qos_ref_1"
    assert (intent.sst, intent.sd, intent.dnn) == (2, "000002", "internet")
    assert intent.gbr_ul_kbps == 1000
    assert intent.mbr_dl_kbps == 1_000_000
    assert intent.gbr_dl_kbps is None


def test_intent_rejects_components_with_different_qos_references() -> None:
    req = _req(medComponents={
        "1": {"medCompN": 1, "qosReference": "qos_ref_1"},
        "2": {"medCompN": 2, "qosReference": "qos_ref_2"},
    })
    with pytest.raises(ValueError):
        PolicyAuthorizationService._intent(req)


def test_intent_without_media_components_is_empty() -> None:
    intent = PolicyAuthorizationService._intent(_req())
    assert intent.qos_reference is None and intent.mbr_dl_kbps is None


def test_termination_request_goes_to_notif_uri_terminate() -> None:
    """Like the Open5GS PCF (ADR 0007), with the spec's TerminationInfo body."""
    stored = {"context": {"ascReqData": {"notifUri": "http://sgw:8500/npcf-policyauthorization/v1/notify/"}},
              "resourceUri": RESOURCE_URI}
    url, payload = build_termination_request(stored)
    assert url == "http://sgw:8500/npcf-policyauthorization/v1/notify/terminate"
    assert payload == {"termCause": "INSUFFICIENT_QOS_FLOW_RESOURCES", "resUri": RESOURCE_URI}


def test_no_termination_request_without_notif_uri() -> None:
    assert build_termination_request({"context": {"ascReqData": {}}, "resourceUri": RESOURCE_URI}) == (None, None)


def test_dual_stack_request_is_accepted_and_no_address_is_rejected() -> None:
    assert _req(ueIpv6="2001:db8::1").ueIpv6 == "2001:db8::1"
    with pytest.raises(ValueError):
        AppSessionContextReqData.model_validate({"notifUri": "x", "suppFeat": "0"})
