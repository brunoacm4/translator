# coding: utf-8

"""
Slice Manager Mock Server

Lightweight FastAPI application that mimics the four SM slice endpoints
so the Translator can be developed and tested locally without VPN access
to the real IT Aveiro testbed.

Behaviour mirrors the real SM (``ran_service.py``):
 - All endpoints accept a JSON body and return **empty body** (HTTP 200).
 - Payloads are logged to stdout for debugging.

Usage:
    uvicorn mock.sm_mock_server:app --port 9090 --reload

Then set SM_BASE_URL=http://localhost:9090 when running the translator.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s [MOCK-SM] %(message)s")
logger = logging.getLogger("sm-mock")

app = FastAPI(
    title="Slice Manager Mock",
    version="0.1.0",
    description="Local development mock for the IT Aveiro Slice Manager",
)

# ── In-memory store (optional — helps with debugging) ─────────────────
# Keeps track of slices that have been "created" so you can inspect state.
_slices: dict[str, dict] = {}


def _log_request(endpoint: str, body: dict) -> None:
    """Pretty-print the received payload."""
    logger.info(
        "\n╔══════════════════════════════════════════════╗\n"
        "║  %s\n"
        "╠══════════════════════════════════════════════╣\n"
        "%s\n"
        "╚══════════════════════════════════════════════╝",
        endpoint,
        json.dumps(body, indent=2, default=str),
    )


# ── Slice endpoints ───────────────────────────────────────────────────

@app.post("/core/slice/create")
async def core_slice_create(request: Request) -> Response:
    """Mimics POST /core/slice/create — stores slice, returns empty 200."""
    body = await request.json()
    slice_id = body.get("id", "unknown")
    _slices[slice_id] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": body,
    }
    _log_request("POST /core/slice/create", body)
    return Response(status_code=200)


@app.post("/core/slice/associate")
async def core_slice_associate(request: Request) -> Response:
    """Mimics POST /core/slice/associate — returns empty 200."""
    body = await request.json()
    slice_id = body.get("slice", "unknown")
    if slice_id in _slices:
        _slices[slice_id]["associated"] = True
        _slices[slice_id]["associate_payload"] = body
    _log_request("POST /core/slice/associate", body)
    return Response(status_code=200)


@app.post("/core/slice/change")
async def core_slice_change(request: Request) -> Response:
    """Mimics POST /core/slice/change — returns empty 200."""
    body = await request.json()
    _log_request("POST /core/slice/change", body)
    return Response(status_code=200)


@app.post("/core/slice/delete")
async def core_slice_delete(request: Request) -> Response:
    """Mimics POST /core/slice/delete — removes slice, returns empty 200."""
    body = await request.json()
    slice_id = body.get("id", "unknown")
    _slices.pop(slice_id, None)
    _log_request("POST /core/slice/delete", body)
    return Response(status_code=200)


# ── Debug helpers ─────────────────────────────────────────────────────

@app.get("/debug/slices")
async def debug_slices() -> dict:
    """Returns all slices currently held in memory (dev convenience)."""
    return {"slices": _slices}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9090)
