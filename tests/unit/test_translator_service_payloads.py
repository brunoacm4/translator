from __future__ import annotations

from app.config.settings import settings
from app.impl.translator_service import TranslatorService
from app.models.nef.subscription import AsSessionWithQoSSubscription


def test_create_payload_includes_configured_coverage_area() -> None:
    service = TranslatorService()
    body = AsSessionWithQoSSubscription(
        notificationDestination="http://localhost:9999/callback",
        dnn="internet",
    )
    old_coverage_area = settings.sm_default_coverage_area
    settings.sm_default_coverage_area = ["IT"]
    try:
        payload = service._build_create_payload(
            body=body,
            slice_id="slice-1",
            qos=None,
            sst=1,
            sd="000001",
            dnn="internet",
        )
    finally:
        settings.sm_default_coverage_area = old_coverage_area

    assert payload["coverage_area"] == ["IT"]


def test_create_payload_uses_new_field_names() -> None:
    """Create payload must use snake_case field names matching the new SM API."""
    service = TranslatorService()
    body = AsSessionWithQoSSubscription(
        notificationDestination="http://localhost:9999/callback",
        dnn="internet",
    )
    payload = service._build_create_payload(
        body=body,
        slice_id="slice-1",
        qos=None,
        sst=1,
        sd="000001",
        dnn="internet",
    )
    assert payload["slice_id"] == "slice-1"
    # Old field names must be gone
    assert "id" not in payload
    assert "dllatency" not in payload
    assert "ullatency" not in payload
    assert "prioritylabel" not in payload


def test_associate_payload_uses_new_rest_field_names() -> None:
    """Associate payload must use the UeSliceAssociationCreateRequest contract."""
    service = TranslatorService()
    body = AsSessionWithQoSSubscription(
        notificationDestination="http://localhost:9999/callback",
        ueIpv4Addr="10.0.0.1",
        dnn="internet",
    )

    payload = service._build_associate_payload(
        body=body,
        slice_id="slice-1",
        snssai="1-000001",
    )

    assert payload["slice_id"] == "slice-1"
    assert payload["snssai"] == "1-000001"
    assert payload["static_ipv4_address"] == "10.0.0.1"
    assert payload["access_mobility_data"] is True
    assert payload["default_association"] is True
    assert payload["snssai_advertisement_allowed"] is True
    # Old field names must be absent
    assert "imsi" not in payload
    assert "slice" not in payload
    assert "dnn" not in payload
    assert "numIMSIs" not in payload
    assert "ipv6" not in payload


def test_change_payload_uses_new_rest_field_names() -> None:
    """Change payload must use the UeSliceAssociationUpdateRequest contract."""
    service = TranslatorService()

    payload = service._build_change_payload(
        slice_id="slice-1",
        snssai="1-000001",
        dnn="internet",
    )

    assert payload["slice_id"] == "slice-1"
    assert payload["dnn"] == "internet"
    assert payload["snssai"] == "1-000001"
    # Old field names must be absent
    assert "imsi" not in payload
    assert "slice" not in payload
