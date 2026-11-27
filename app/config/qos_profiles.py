# coding: utf-8

"""
QoS profile catalog (docs/adr/0005)

Maps each ``qosReference`` the translator accepts to the Slice Manager slice
that implements it. The SM pre-creates those slices (model B, docs/adr/0004);
the translator only associates, moves and dissociates UEs.

The SM selects the slice by ``slice_id``. It reads the DNN and S-NSSAI from
the slice itself, but its schema still requires both in the associate and
move requests, so they are kept here too.

The entries below are PLACEHOLDERS until the SM team sends the list of slices
it provisions. They share one S-NSSAI and DNN and differ only in the slice
(the SM calls each one a "DNN QoS template"), which is how the SM models
several QoS levels for the same service.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from app.config import testbed_defaults as tb


@dataclass(frozen=True)
class QoSProfile:
    """One QoS profile: the pre-provisioned SM slice that implements it."""

    slice_id: str             # SM slice identifier ("QoS profile selector")
    sst: int                  # S-NSSAI of that slice
    sd: Optional[str]         # 6 hex digits, e.g. "000001"
    dnn: str                  # DNN of that slice

    @property
    def sm_snssai(self) -> str:
        """S-NSSAI in the SM's notation, e.g. ``1-000001``."""
        return f"{self.sst}-{self.sd}" if self.sd else str(self.sst)


# ── Placeholder catalog (replace with the SM team's slice list) ─────────
QOS_PROFILES: Dict[str, QoSProfile] = {
    "qos_ref_1": QoSProfile(slice_id="placeholder-slice-1", sst=1, sd="000001", dnn=tb.DEFAULT_DNN),
    "qos_ref_2": QoSProfile(slice_id="placeholder-slice-2", sst=1, sd="000001", dnn=tb.DEFAULT_DNN),
    "qos_ref_3": QoSProfile(slice_id="placeholder-slice-3", sst=1, sd="000001", dnn=tb.DEFAULT_DNN),
}


def resolve_qos_profile(qos_reference: str) -> QoSProfile:
    """
    Look up a QoS profile by its 3GPP reference string.

    Raises:
        ValueError: If the reference is not found in the catalog.
    """
    profile = QOS_PROFILES.get(qos_reference)
    if profile is None:
        known = ", ".join(sorted(QOS_PROFILES.keys()))
        raise ValueError(
            f"Unknown qos_reference '{qos_reference}'. "
            f"Known profiles: [{known}]"
        )
    return profile
