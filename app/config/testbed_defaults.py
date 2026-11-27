# coding: utf-8

"""
Testbed Defaults for the Porto de Aveiro network.

Hardcoded values for fields required by ``UeSliceAssociationCreateRequest``
that the 3GPP NEF does not provide.  These match the Aveiro testbed
configuration and will be replaced by dynamic lookup once the full
infrastructure is connected.
"""

from __future__ import annotations

# ── Associate endpoint required fields ────────────────────────────────

# Aggregate Maximum Bit Rate — uplink (kbps).
DEFAULT_AMBR_UP: int = 100000

# Aggregate Maximum Bit Rate — downlink (kbps).
DEFAULT_AMBR_DW: int = 100000

# Default DNN (Data Network Name) when the NEF request doesn't specify one.
DEFAULT_DNN: str = "internet"
