from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from app.config.settings import settings
from app.db.connection import close_connection
from app.impl.sm_client import SliceManagerClient


@pytest.fixture
def sqlite_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "translator_test.db"
    old_path = settings.translator_db_path
    close_connection()
    settings.translator_db_path = str(db_path)
    yield db_path
    close_connection()
    settings.translator_db_path = old_path


@dataclass
class FakeSM:
    """Records every SM write the translator makes (model B: no slice writes).

    ``request_ids`` sets the SM ``request_id`` each write returns; the empty
    default means "the SM returned none", so nothing is polled.
    """

    associate: List[Tuple[str, dict]] = field(default_factory=list)
    change: List[Tuple[str, dict]] = field(default_factory=list)
    dissociate: List[Tuple[str, str]] = field(default_factory=list)
    request_ids: Dict[str, str] = field(
        default_factory=lambda: {"associate": "", "change": "", "dissociate": ""}
    )


@pytest.fixture
def fake_sm(monkeypatch) -> FakeSM:
    """Replace the SM writes with in-memory fakes."""
    sm = FakeSM()

    async def _associate(self, ue_id, payload):
        sm.associate.append((ue_id, payload))
        return sm.request_ids["associate"]

    async def _change(self, ue_id, payload):
        sm.change.append((ue_id, payload))
        return sm.request_ids["change"]

    async def _dissociate(self, ue_id, slice_id):
        sm.dissociate.append((ue_id, slice_id))
        return sm.request_ids["dissociate"]

    monkeypatch.setattr(SliceManagerClient, "associate_slice", _associate)
    monkeypatch.setattr(SliceManagerClient, "change_slice", _change)
    monkeypatch.setattr(SliceManagerClient, "dissociate_slice", _dissociate)
    return sm
