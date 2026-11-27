"""Profile resolution and SM payloads under model B (docs/adr/0005, 0006)."""

from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.impl.session_core import QosIntent, SessionCore, resolve_qos
from app.impl.translator_service import TranslatorService
from app.models.nef.subscription import AsSessionWithQoSSubscription


def test_the_profile_selects_the_slice() -> None:
    resolved = resolve_qos(QosIntent(qos_reference="qos_ref_2"))
    assert resolved.slice_id == "placeholder-slice-2"
    assert resolved.sm_snssai == "1-000001"
    assert resolved.dnn == "internet"


def test_a_profile_is_required() -> None:
    with pytest.raises(ValueError, match="qosReference is required"):
        resolve_qos(QosIntent())


def test_an_unknown_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown qos_reference"):
        resolve_qos(QosIntent(qos_reference="no_such_profile"))


@pytest.mark.parametrize(
    "intent",
    [
        QosIntent(qos_reference="qos_ref_1", sst=2),
        QosIntent(qos_reference="qos_ref_1", sst=1, sd="000002"),
        QosIntent(qos_reference="qos_ref_1", dnn="ims"),
    ],
)
def test_snssai_or_dnn_that_differ_from_the_slice_are_rejected(intent) -> None:
    with pytest.raises(ValueError, match="does not match"):
        resolve_qos(intent)


def test_matching_snssai_and_dnn_are_accepted() -> None:
    for intent in (
        QosIntent(qos_reference="qos_ref_1", sst=1),
        QosIntent(qos_reference="qos_ref_1", sst=1, sd="000001", dnn="internet"),
    ):
        assert resolve_qos(intent).slice_id == "placeholder-slice-1"


def test_tsc_qos_req_is_parsed_but_does_not_choose_the_slice() -> None:
    """Applying tscQosReq is decision D3; today only qosReference matters."""
    body = AsSessionWithQoSSubscription(
        notificationDestination="http://localhost:9999/callback",
        qosReference="qos_ref_1",
        tscQosReq={"req5Gsdelay": 7, "priority": 3, "reqGbrDl": "10 Mbps", "reqMbrUl": "500 Kbps"},
    )
    intent = TranslatorService._intent(body)
    assert (intent.latency_ms, intent.priority) == (7, 3)
    assert (intent.gbr_dl_kbps, intent.mbr_ul_kbps) == (10000, 500)
    assert resolve_qos(intent).slice_id == "placeholder-slice-1"


def test_slice_creation_settings_are_gone() -> None:
    """Model B: the translator never creates slices (docs/adr/0004)."""
    assert "sm_default_coverage_area" not in Settings.model_fields
    assert "sm_default_n6_protection" not in Settings.model_fields
    assert not hasattr(SessionCore, "build_create_payload")


def test_associate_payload_uses_new_rest_field_names() -> None:
    """Associate payload must use the UeSliceAssociationCreateRequest contract."""
    payload = SessionCore.build_associate_payload(
        slice_id="slice-1",
        snssai="1-000001",
        dnn="internet",
    )

    assert payload["slice_id"] == "slice-1"
    assert payload["snssai"] == "1-000001"
    # dnn is required by the SM UeSliceAssociationCreateRequest contract
    assert payload["dnn"] == "internet"
    assert payload["access_mobility_data"] is True
    assert payload["snssai_advertisement_allowed"] is True
    # The UE IP only resolves the IMSI; it must not be pinned as a static IP
    assert "static_ipv4_address" not in payload
    # Not part of the SM UeSliceAssociationCreateRequest contract
    assert "default_association" not in payload
    # Old field names must be absent
    assert "imsi" not in payload
    assert "slice" not in payload
    assert "numIMSIs" not in payload
    assert "ipv6" not in payload


def test_change_payload_uses_new_rest_field_names() -> None:
    """Change payload must use the UeSliceAssociationUpdateRequest contract."""
    payload = SessionCore.build_change_payload(
        slice_id="slice-1",
        snssai="1-000001",
        dnn="internet",
    )

    assert payload == {"slice_id": "slice-1", "snssai": "1-000001", "dnn": "internet"}
