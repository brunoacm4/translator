from __future__ import annotations

from fastapi import Request

from app.utils.idempotency import build_payload_fingerprint, extract_idempotency_key


def _make_request(headers: dict[str, str]) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    return Request(scope)


def test_extract_idempotency_key_normalized() -> None:
    request = _make_request({"Idempotency-Key": "  abc-123  "})
    assert extract_idempotency_key(request) == "abc-123"


def test_extract_idempotency_key_missing() -> None:
    request = _make_request({})
    assert extract_idempotency_key(request) is None


def test_fingerprint_is_deterministic_for_key_order() -> None:
    payload_a = {"notificationDestination": "http://x", "ueIpv4Addr": "10.0.0.1"}
    payload_b = {"ueIpv4Addr": "10.0.0.1", "notificationDestination": "http://x"}

    fp_a = build_payload_fingerprint("app1", payload_a)
    fp_b = build_payload_fingerprint("app1", payload_b)

    assert fp_a == fp_b
