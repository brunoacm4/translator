# coding: utf-8

from __future__ import annotations

import sqlite3

from app.db.connection import get_connection

# Column that only databases created before 2026-11-27 have; their operations
# table no longer matches what the translator writes.
_OBSOLETE_OPERATIONS_COLUMN = "payload_fingerprint"


def init_db() -> None:
    """Create translator SQLite tables and indexes if they don't exist.

    Raises:
        RuntimeError: the database was created by an older version, whose
            schema this version does not migrate. Recreate it.
    """
    conn = get_connection()
    _reject_obsolete_schema(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            scs_as_id TEXT NOT NULL,
            subscription_id TEXT NOT NULL,
            sm_slice_id TEXT NOT NULL,
            imsi TEXT NOT NULL,
            operation_id TEXT,
            subscription_data TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (scs_as_id, subscription_id)
        );

        CREATE TABLE IF NOT EXISTS operations (
            operation_id TEXT PRIMARY KEY,
            scs_as_id TEXT NOT NULL,
            status TEXT NOT NULL,
            subscription_id TEXT,
            sm_slice_id TEXT,
            sm_request_id TEXT,
            -- Slice a QoS change moved away from; restored if the change fails (docs/adr/0006)
            previous_sm_slice_id TEXT,
            error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS idempotency_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scs_as_id TEXT NOT NULL,
            idempotency_key TEXT,
            payload_fingerprint TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS ix_operations_scs_status
            ON operations (scs_as_id, status);

        CREATE UNIQUE INDEX IF NOT EXISTS ux_idempotency_scs_fingerprint
            ON idempotency_keys (scs_as_id, payload_fingerprint);

        CREATE UNIQUE INDEX IF NOT EXISTS ux_idempotency_scs_key_not_null
            ON idempotency_keys (scs_as_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL;

        CREATE INDEX IF NOT EXISTS ix_idempotency_operation_id
            ON idempotency_keys (operation_id);

        CREATE INDEX IF NOT EXISTS ix_subscriptions_imsi
            ON subscriptions (imsi);
        """
    )
    conn.commit()


def _reject_obsolete_schema(conn: sqlite3.Connection) -> None:
    """Stop at startup on a database from before 2026-11-27.

    Its operations table still requires columns the translator no longer
    writes, so every create would fail. The testbed keeps no state worth
    migrating: the database is recreated instead.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(operations)")}
    if _OBSOLETE_OPERATIONS_COLUMN in columns:
        raise RuntimeError(
            "The translator database was created by an older version. Recreate it: "
            "`docker compose -f infra/docker-compose.yml down -v`, or delete the file "
            "at TRANSLATOR_DB_PATH."
        )
