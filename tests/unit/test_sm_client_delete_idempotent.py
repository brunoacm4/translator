from __future__ import annotations

import asyncio

import httpx

from app.impl import sm_client as sm_client_mod
from app.impl.sm_client import SliceManagerClient


def _install_mock_transport(monkeypatch, handler) -> None:
    """Point the shared SM httpx client at an in-memory MockTransport."""
    client = httpx.AsyncClient(
        base_url="http://sm.test",
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(sm_client_mod, "_shared_http_client", client)


def test_delete_slice_tolerates_404(monkeypatch) -> None:
    """A 404 from DELETE /core/slices/{id} is treated as success (idempotent).

    The SM's delete_slice does an existence check (gRPC GetSlice) and returns
    404 when the slice is unknown — e.g. against the no-op sandbox whose read
    model only contains canned demo data. Deleting an already-absent slice is
    the desired end state, so the client must not raise.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "slice 'x' not found"})

    _install_mock_transport(monkeypatch, handler)

    result = asyncio.run(SliceManagerClient().delete_slice("s1d000001-internet"))
    assert result == ""


def test_dissociate_slice_tolerates_404(monkeypatch) -> None:
    """A 404 from DELETE /core/ues/{id}/slice-associations/{slice} is success."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "association not found"})

    _install_mock_transport(monkeypatch, handler)

    result = asyncio.run(
        SliceManagerClient().dissociate_slice("999080100001151", "s1d000001-internet")
    )
    assert result == ""
