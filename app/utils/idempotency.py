# coding: utf-8

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from fastapi import Request


def extract_idempotency_key(request: Request) -> Optional[str]:
    """Read and normalize Idempotency-Key header."""
    raw = request.headers.get("Idempotency-Key")
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def build_payload_fingerprint(scs_as_id: str, body: Dict[str, Any]) -> str:
    """Build deterministic SHA-256 fingerprint from request payload."""
    canonical = json.dumps(
        {
            "scs_as_id": scs_as_id,
            "body": body,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
