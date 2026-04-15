from __future__ import annotations

from app.config.settings import settings
from app.impl.translator_service import TranslatorService
from app.models.nef.subscription import AsSessionWithQoSSubscription


def test_create_payload_includes_configured_ran() -> None:
    service = TranslatorService()
    body = AsSessionWithQoSSubscription(
        notificationDestination="http://localhost:9999/callback",
        dnn="internet",
    )
    old_ran = settings.sm_default_ran
    settings.sm_default_ran = "IT"
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
        settings.sm_default_ran = old_ran

    assert payload["ran"] == "IT"


def test_associate_payload_uses_canonical_lowercase_contract() -> None:
    service = TranslatorService()
    body = AsSessionWithQoSSubscription(
        notificationDestination="http://localhost:9999/callback",
        ueIpv4Addr="10.0.0.1",
        dnn="internet",
    )

    payload = service._build_associate_payload(
        body=body,
        slice_id="slice-1",
        imsi="268019012345678",
        dnn="internet",
        snssai="1-000001",
    )

    assert payload["imsi"] == "268019012345678"
    assert payload["slice"] == "slice-1"
    assert payload["dnn"] == "internet"
    assert payload["snssai"] == "1-000001"
    assert "SNSSAI" not in payload
    assert "DNN" not in payload
    assert "DNNQOSTPLID" not in payload
    assert "DEFAULT" not in payload


def test_change_payload_uses_sm_required_fields() -> None:
    service = TranslatorService()

    payload = service._build_change_payload(
        imsi="268019012345678",
        slice_id="slice-1",
        snssai="1-000001",
        dnn="internet",
    )

    assert payload["imsi"] == "268019012345678"
    assert payload["slice"] == "slice-1"
    assert payload["dnn"] == "internet"
    assert payload["snssai"] == "1-000001"
    assert "DNNQOSTPLID" not in payload
    assert "SNSSAI" not in payload
