# coding: utf-8

from __future__ import annotations

from app.db.connection import get_connection


def init_db() -> None:
    """Create translator SQLite tables and indexes if they don't exist."""
    conn = get_connection()
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
            idempotency_key TEXT,
            payload_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL,
            subscription_id TEXT,
            sm_slice_id TEXT,
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

        CREATE UNIQUE INDEX IF NOT EXISTS ux_subscriptions_scs_sub
            ON subscriptions (scs_as_id, subscription_id);

        CREATE INDEX IF NOT EXISTS ix_subscriptions_sm_slice
            ON subscriptions (sm_slice_id);

        CREATE UNIQUE INDEX IF NOT EXISTS ux_operations_operation_id
            ON operations (operation_id);

        CREATE INDEX IF NOT EXISTS ix_operations_scs_status
            ON operations (scs_as_id, status);

        CREATE UNIQUE INDEX IF NOT EXISTS ux_idempotency_scs_fingerprint
            ON idempotency_keys (scs_as_id, payload_fingerprint);

        CREATE UNIQUE INDEX IF NOT EXISTS ux_idempotency_scs_key_not_null
            ON idempotency_keys (scs_as_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL;

        CREATE INDEX IF NOT EXISTS ix_idempotency_operation_id
            ON idempotency_keys (operation_id);

        CREATE TABLE IF NOT EXISTS slice_registry (
            snssai TEXT NOT NULL,
            dnn TEXT NOT NULL,
            sm_slice_id TEXT NOT NULL,
            ref_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (snssai, dnn)
        );

        CREATE INDEX IF NOT EXISTS ix_slice_registry_slice_id
            ON slice_registry (sm_slice_id);
        """
    )
    conn.commit()
