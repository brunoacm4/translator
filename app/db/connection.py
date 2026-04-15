# coding: utf-8

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager

from app.config.settings import settings

_CONN: sqlite3.Connection | None = None
_LOCK = threading.Lock()


def get_connection() -> sqlite3.Connection:
    """Return a process-wide SQLite connection."""
    global _CONN
    if _CONN is None:
        with _LOCK:
            if _CONN is None:
                conn = sqlite3.connect(
                    settings.translator_db_path,
                    check_same_thread=False,
                )
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA journal_mode = WAL")
                _CONN = conn
    return _CONN


def close_connection() -> None:
    """Close the shared SQLite connection (mainly for tests)."""
    global _CONN
    if _CONN is not None:
        _CONN.close()
        _CONN = None


@contextmanager
def transaction() -> sqlite3.Cursor:
    """Run SQL statements inside an explicit transaction."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
