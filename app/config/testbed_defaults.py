# coding: utf-8

"""
Testbed Defaults for the Porto de Aveiro network.

Hardcoded values the translator sends or uses that the 3GPP requests do not
provide. They match the Aveiro testbed configuration.
"""

from __future__ import annotations

# ── Associate request (UeSliceAssociationCreateRequest) ────────────────
# Optional in the SM since main bfbaf29 (only dnn, snssai and slice_id are
# required). Sending them sets the UE's AMBR; whether to keep doing so belongs
# to decision D3 (per-request bitrates).

# Aggregate Maximum Bit Rate — uplink (kbps).
DEFAULT_AMBR_UP: int = 100000

# Aggregate Maximum Bit Rate — downlink (kbps).
DEFAULT_AMBR_DW: int = 100000

# DNN (Data Network Name) of the placeholder QoS profiles (app/config/qos_profiles.py).
DEFAULT_DNN: str = "internet"
