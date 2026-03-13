# coding: utf-8

"""
Unit conversion utilities for 3GPP → Slice Manager translation.
"""

from __future__ import annotations

import re
from typing import Optional


def mbps_to_kbps(value_mbps: float) -> int:
    """
    Convert Megabits per second to Kilobits per second.

    The Slice Manager expects throughput values in KBPS (integer).
    The 3GPP NEF sends them in Mbps (float).

    Args:
        value_mbps: Bandwidth in Mbps.

    Returns:
        Bandwidth in KBPS (rounded to nearest integer).

    Examples:
        >>> mbps_to_kbps(10.0)
        10240
        >>> mbps_to_kbps(50.0)
        51200
        >>> mbps_to_kbps(0.5)
        512
    """
    return round(value_mbps * 1024)


# ── 3GPP BitRate string parser ─────────────────────────────────────────
# The 3GPP ``BitRate`` type (TS 29.571) is a string like:
#   "10 Mbps",  "1 Gbps",  "500 Kbps",  "128000 bps"
_BITRATE_RE = re.compile(
    r"^\s*(?P<value>[\d.]+)\s*(?P<unit>[KMGT]?bps)\s*$",
    re.IGNORECASE,
)

_BITRATE_UNIT_TO_KBPS = {
    "bps":  1 / 1000,
    "kbps": 1,
    "mbps": 1000,
    "gbps": 1_000_000,
    "tbps": 1_000_000_000,
}


def parse_bitrate_to_kbps(bitrate_str: str) -> Optional[int]:
    """
    Parse a 3GPP BitRate string to KBPS (integer).

    Args:
        bitrate_str: e.g. ``"10 Mbps"``, ``"500 Kbps"``, ``"1 Gbps"``

    Returns:
        KBPS as int, or None if the string cannot be parsed.

    Examples:
        >>> parse_bitrate_to_kbps("10 Mbps")
        10000
        >>> parse_bitrate_to_kbps("1 Gbps")
        1000000
        >>> parse_bitrate_to_kbps("500 Kbps")
        500
    """
    m = _BITRATE_RE.match(bitrate_str)
    if not m:
        return None
    value = float(m.group("value"))
    unit = m.group("unit").lower()
    multiplier = _BITRATE_UNIT_TO_KBPS.get(unit)
    if multiplier is None:
        return None
    return round(value * multiplier)
