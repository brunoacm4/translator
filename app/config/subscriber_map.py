# coding: utf-8

"""
UE IP → IMSI static resolver for the Porto de Aveiro testbed.

The 3GPP NEF spec identifies UEs by ``ueIpv4Addr`` / ``ueIpv6Addr``.
The SM needs an IMSI.  This module bridges the two.

In production this would query the UDM or a subscriber database.
For the testbed MVP, a static dictionary is sufficient — populate it
with the SIM cards deployed in the Aveiro UEs.
"""

from __future__ import annotations

from typing import Dict


# ── Static subscriber maps ────────────────────────────────────────────
# Key:   UE IP address as the NEF sends it
# Value: IMSI string as expected by the SM associate endpoint

IPV4_TO_IMSI: Dict[str, str] = {
    # TODO: Populate with real testbed UE IPs → IMSIs
    "10.0.0.1": "268019012345678",
    "10.0.0.2": "268019012345679",
}

IPV6_TO_IMSI: Dict[str, str] = {
    # TODO: Populate with real testbed UE IPv6 → IMSIs
    "fd00::1": "268019012345678",
    "fd00::2": "268019012345679",
}

# Legacy MSISDN map kept for backward compatibility / future use
MSISDN_TO_IMSI: Dict[str, str] = {
    "msisdn-351912345678": "268019012345678",
    "msisdn-351912345679": "268019012345679",
}


def resolve_imsi(
    ue_ipv4: str | None = None,
    ue_ipv6: str | None = None,
) -> str:
    """
    Resolve a UE IP address to an IMSI.

    Tries IPv4 first, then IPv6.

    Args:
        ue_ipv4: UE IPv4 address from ``ueIpv4Addr``.
        ue_ipv6: UE IPv6 address from ``ueIpv6Addr``.

    Returns:
        IMSI string.

    Raises:
        ValueError: If neither address resolves to a known IMSI.
    """
    if ue_ipv4:
        imsi = IPV4_TO_IMSI.get(ue_ipv4)
        if imsi:
            return imsi
    if ue_ipv6:
        imsi = IPV6_TO_IMSI.get(ue_ipv6)
        if imsi:
            return imsi

    tried = ", ".join(filter(None, [ue_ipv4, ue_ipv6]))
    raise ValueError(
        f"Could not resolve IMSI for UE address(es): {tried}. "
        f"Not found in the subscriber map. "
        f"Register this UE in subscriber_map.py or the UDM."
    )
