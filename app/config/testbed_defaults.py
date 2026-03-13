# coding: utf-8

"""
Testbed Defaults for the Porto de Aveiro network.

Hardcoded values for fields required by ``CoreSliceAssociatePostRequest``
that the 3GPP NEF does not provide.  These match the Aveiro testbed
configuration and will be replaced by dynamic lookup once the full
infrastructure is connected.

IMPORTANT — types must match the SM's Pydantic models exactly:
  - ``default`` / ``uecanSendSNSSAI`` → StrictStr  ("true" / "false")
  - ``ambrup`` / ``ambrdw``           → StrictInt
  - ``dnnqostplid``                   → StrictInt
"""

from __future__ import annotations

# ── Associate endpoint required fields ────────────────────────────────

# Number of IMSIs covered by this slice association (1 = single UE).
NUM_IMSIS: int = 1

# IPv4 address assigned to the UE on this slice.
# In a real setup this would come from the UPF / SMF.
DEFAULT_IPV4: str = "10.0.0.1"

# IPv6 prefix assigned to the UE.
DEFAULT_IPV6: str = "fd00::1"

# AM Data (Access and Mobility) — testbed default.
DEFAULT_AMDATA: str = "default"

# Whether this is the default slice for the UE.
# SM expects StrictStr "true" / "false", NOT a Python bool.
DEFAULT_SLICE_FLAG: str = "true"

# Whether the UE can send S-NSSAI in registration.
# SM expects StrictStr "true" / "false".
UE_CAN_SEND_SNSSAI: str = "true"

# Aggregate Maximum Bit Rate — uplink (KBPS).
# SM expects StrictInt.
DEFAULT_AMBR_UP: int = 100000

# Aggregate Maximum Bit Rate — downlink (KBPS).
# SM expects StrictInt.
DEFAULT_AMBR_DW: int = 100000

# Bit-rate units sent alongside AMBR values.
DEFAULT_UP_UNIT: str = "KBPS"
DEFAULT_DW_UNIT: str = "KBPS"

# Default DNN (Data Network Name) when the NEF request doesn't specify one.
DEFAULT_DNN: str = "internet"

# Default DNN QoS Template ID.
# SM expects StrictInt.
DEFAULT_DNN_QOS_TPL_ID: int = 1
