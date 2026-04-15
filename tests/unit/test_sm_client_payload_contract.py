from __future__ import annotations

from app.impl.sm_client import SliceManagerClient


def test_sanitize_associate_payload_whitelists_and_canonicalizes() -> None:
    payload = {
        "imsi": "268019012345679",
        "slice": "slice-1",
        "numIMSIs": 1,
        "ipv4": "10.0.0.2",
        "ipv6": "fd00::1",
        "amdata": "true",
        "default": "true",
        "uecanSendSNSSAI": "true",
        "ambrup": 100000,
        "ambrdw": 100000,
        "snssai": "1-000001",
        "upUnit": "KBPS",
        "dwUnit": "KBPS",
        "dnn": "internet",
        "ignored": "value",
    }

    sanitized = SliceManagerClient._sanitize_payload("/core/slice/associate", payload)

    assert sanitized["numimsis"] == 1
    assert sanitized["uecansendsnssai"] == "true"
    assert sanitized["snssai"] == "1-000001"
    assert "numIMSIs" not in sanitized
    assert "uecanSendSNSSAI" not in sanitized
    assert "upUnit" not in sanitized
    assert "dwUnit" not in sanitized
    assert "dnn" not in sanitized
    assert "ignored" not in sanitized


def test_sanitize_change_payload_drops_unsupported_legacy_keys() -> None:
    payload = {
        "imsi": "268019012345679",
        "slice": "slice-1",
        "dnn": "internet",
        "snssai": "1-000001",
        "dnnqostplid": 1,
        "extra": "x",
    }

    sanitized = SliceManagerClient._sanitize_payload("/core/slice/change", payload)

    assert set(sanitized.keys()) == {"imsi", "slice", "dnn", "snssai"}
    assert "dnnqostplid" not in sanitized
    assert "extra" not in sanitized


def test_sanitize_create_payload_drops_non_contract_fields() -> None:
    payload = {
        "id": "slice-1",
        "sst": 1,
        "sd": "000001",
        "dnn": "internet",
        "ran": "IT",
        "prioritylabel": 30,
    }

    sanitized = SliceManagerClient._sanitize_payload("/core/slice/create", payload)

    assert sanitized["id"] == "slice-1"
    assert sanitized["sst"] == 1
    assert sanitized["sd"] == "000001"
    assert sanitized["dnn"] == "internet"
    assert sanitized["prioritylabel"] == 30
    assert "ran" not in sanitized


def test_sanitize_delete_payload_keeps_only_id() -> None:
    payload = {"id": "slice-1", "extra": "x"}

    sanitized = SliceManagerClient._sanitize_payload("/core/slice/delete", payload)

    assert sanitized == {"id": "slice-1"}
