# coding: utf-8

"""
QoS Reference Mapping Table

Maps 3GPP qos_reference strings to Slice Manager parameters.
Each profile defines the SST, optional SD, default 5QI, target latency,
and reliability class expected by the SM's CoreSliceCreatePostRequest.

In production this should be loaded from a YAML/JSON config file or a
database. For the Porto de Aveiro testbed MVP, a static dict suffices.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class QoSProfile:
    """Resolved QoS parameters for a given reference."""

    sst: int                          # Slice/Service Type (1=eMBB, 2=URLLC, 3=MIoT)
    sd: Optional[str]                 # Slice Differentiator (hex string, e.g. "000001")
    default_5qi: int                  # 5G QoS Identifier
    latency_ms: int                   # Target latency in milliseconds
    reliability_percent: float        # e.g. 99.9, 99.999

    # Extended fields used by core_consumer.py for QCI / scheduling logic
    prioritylabel: int = 0            # 0–100, higher = more priority
    delaytolerance: str = "NOT_SUPPORTED"  # SUPPORTED / NOT_SUPPORTED
    dldeterministiccomm: str = "NOT_SUPPORTED"  # SUPPORTED / NOT_SUPPORTED
    uldeterministiccomm: str = "NOT_SUPPORTED"  # SUPPORTED / NOT_SUPPORTED
    uemobilitylevel: str = "fully_mobility"  # stationary / nomadic / restricted_mobility / fully_mobility


# ── Static mapping table for the Porto de Aveiro testbed ──────────────
QOS_PROFILES: Dict[str, QoSProfile] = {
    # eMBB — Enhanced Mobile Broadband
    "qos_ref_1": QoSProfile(
        sst=1,
        sd="000001",
        default_5qi=9,
        latency_ms=20,
        reliability_percent=99.9,
        prioritylabel=30,
        delaytolerance="NOT_SUPPORTED",
        dldeterministiccomm="NOT_SUPPORTED",
        uldeterministiccomm="NOT_SUPPORTED",
        uemobilitylevel="fully_mobility",
    ),
    # URLLC — Ultra-Reliable Low-Latency Communications
    "qos_ref_2": QoSProfile(
        sst=2,
        sd="000002",
        default_5qi=7,
        latency_ms=5,
        reliability_percent=99.999,
        prioritylabel=80,
        delaytolerance="NOT_SUPPORTED",
        dldeterministiccomm="SUPPORTED",
        uldeterministiccomm="SUPPORTED",
        uemobilitylevel="restricted_mobility",
    ),
    # MIoT — Massive IoT
    "qos_ref_3": QoSProfile(
        sst=3,
        sd="000003",
        default_5qi=9,
        latency_ms=100,
        reliability_percent=99.0,
        prioritylabel=10,
        delaytolerance="SUPPORTED",
        dldeterministiccomm="NOT_SUPPORTED",
        uldeterministiccomm="NOT_SUPPORTED",
        uemobilitylevel="stationary",
    ),
}


def resolve_qos_profile(qos_reference: str) -> QoSProfile:
    """
    Look up a QoS profile by its 3GPP reference string.

    Raises:
        ValueError: If the reference is not found in the mapping table.
    """
    profile = QOS_PROFILES.get(qos_reference)
    if profile is None:
        known = ", ".join(sorted(QOS_PROFILES.keys()))
        raise ValueError(
            f"Unknown qos_reference '{qos_reference}'. "
            f"Known profiles: [{known}]"
        )
    return profile
