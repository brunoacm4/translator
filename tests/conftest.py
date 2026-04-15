from __future__ import annotations

from pathlib import Path

import pytest

from app.config.settings import settings
from app.db.connection import close_connection


@pytest.fixture
def sqlite_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "translator_test.db"
    old_path = settings.translator_db_path
    close_connection()
    settings.translator_db_path = str(db_path)
    yield db_path
    close_connection()
    settings.translator_db_path = old_path
