"""Database schema: created on a fresh file, rejected when it is from an older version."""

from __future__ import annotations

import sqlite3

import pytest

from app.db.connection import get_connection
from app.db.schema import init_db


def _columns(table: str) -> set:
    return {row["name"] for row in get_connection().execute(f"PRAGMA table_info({table})")}


def test_fresh_database_has_only_the_columns_in_use(sqlite_db) -> None:
    init_db()

    assert _columns("operations") == {
        "operation_id", "scs_as_id", "status", "subscription_id", "sm_slice_id",
        "sm_request_id", "previous_sm_slice_id", "error", "created_at", "updated_at",
    }
    init_db()  # a second start on the same file is fine


def test_database_from_an_older_version_is_rejected(sqlite_db) -> None:
    old = sqlite3.connect(sqlite_db)
    old.execute(
        "CREATE TABLE operations (operation_id TEXT PRIMARY KEY, scs_as_id TEXT NOT NULL, "
        "idempotency_key TEXT, payload_fingerprint TEXT NOT NULL, status TEXT NOT NULL)"
    )
    old.commit()
    old.close()

    with pytest.raises(RuntimeError, match="older version"):
        init_db()
