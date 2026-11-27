# coding: utf-8

"""Operations and 29.122 idempotency keys. Sessions live in ``subscription_store``."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db.connection import get_connection, transaction


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OperationRepository:
    """One row per SM write the translator tracks: a create or a QoS change.

    Idempotency of 29.122 creates is kept apart, in ``IdempotencyRepository``.
    """

    _COLUMNS = (
        "operation_id, scs_as_id, status, subscription_id, sm_slice_id, "
        "sm_request_id, previous_sm_slice_id, error, created_at, updated_at"
    )

    def create(self, *, operation_id: str, scs_as_id: str, status: str) -> None:
        now = _now_iso()
        with transaction() as cur:
            cur.execute(
                """
                INSERT INTO operations (operation_id, scs_as_id, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (operation_id, scs_as_id, status, now, now),
            )

    def get(self, operation_id: str) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        row = conn.execute(
            f"SELECT {self._COLUMNS} FROM operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        return self._to_dict(row)

    def update_status(
        self,
        *,
        operation_id: str,
        status: str,
        subscription_id: Optional[str] = None,
        sm_slice_id: Optional[str] = None,
        sm_request_id: Optional[str] = None,
        error: Optional[str] = None,
        previous_sm_slice_id: Optional[str] = None,
    ) -> bool:
        now = _now_iso()
        with transaction() as cur:
            cur.execute(
                """
                UPDATE operations
                SET
                    status = ?,
                    subscription_id = COALESCE(?, subscription_id),
                    sm_slice_id = COALESCE(?, sm_slice_id),
                    sm_request_id = COALESCE(?, sm_request_id),
                    error = COALESCE(?, error),
                    previous_sm_slice_id = COALESCE(?, previous_sm_slice_id),
                    updated_at = ?
                WHERE operation_id = ?
                """,
                (
                    status,
                    subscription_id,
                    sm_slice_id,
                    sm_request_id,
                    error,
                    previous_sm_slice_id,
                    now,
                    operation_id,
                ),
            )
            return cur.rowcount > 0

    def get_resumable(self) -> List[Dict[str, Any]]:
        """Return operations in ``sm_provisioning`` state that have a
        ``sm_request_id`` — these need their polling task resumed after
        a process restart.
        """
        conn = get_connection()
        rows = conn.execute(
            f"""
            SELECT {self._COLUMNS}
            FROM operations
            WHERE status = 'sm_provisioning'
              AND sm_request_id IS NOT NULL
            """,
        ).fetchall()
        return [self._to_dict(r) for r in rows if r]

    @staticmethod
    def _to_dict(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
        return dict(row) if row is not None else None


class IdempotencyRepository:
    def reserve_or_get_existing(
        self,
        *,
        scs_as_id: str,
        idempotency_key: Optional[str],
        payload_fingerprint: str,
        operation_id: str,
    ) -> Dict[str, Any]:
        now = _now_iso()
        try:
            with transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO idempotency_keys (
                        scs_as_id,
                        idempotency_key,
                        payload_fingerprint,
                        operation_id,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        scs_as_id,
                        idempotency_key,
                        payload_fingerprint,
                        operation_id,
                        now,
                    ),
                )
            return {
                "reserved": True,
                "operation_id": operation_id,
                "status": "pending",
                "subscription_id": None,
                "sm_slice_id": None,
            }
        except sqlite3.IntegrityError:
            conn = get_connection()
            params: List[Any] = [scs_as_id, payload_fingerprint]
            where = "k.scs_as_id = ? AND k.payload_fingerprint = ?"
            if idempotency_key is not None:
                where = "k.scs_as_id = ? AND (k.idempotency_key = ? OR k.payload_fingerprint = ?)"
                params = [scs_as_id, idempotency_key, payload_fingerprint]

            row = conn.execute(
                f"""
                SELECT k.operation_id,
                       o.status,
                       o.subscription_id,
                       o.sm_slice_id
                FROM idempotency_keys k
                LEFT JOIN operations o ON o.operation_id = k.operation_id
                WHERE {where}
                ORDER BY k.id DESC
                LIMIT 1
                """,
                params,
            ).fetchone()

            if row is None:
                return {
                    "reserved": False,
                    "operation_id": operation_id,
                    "status": "pending",
                    "subscription_id": None,
                    "sm_slice_id": None,
                }

            return {
                "reserved": False,
                "operation_id": row["operation_id"],
                "status": row["status"] or "pending",
                "subscription_id": row["subscription_id"],
                "sm_slice_id": row["sm_slice_id"],
            }

    def release(self, operation_id: str) -> None:
        """Forget the key/fingerprint reserved by *operation_id*.

        Deduplication only protects a request while its outcome still stands:
        once the create failed or the subscription was deleted, an identical
        request (e.g. a camera reconnecting) must create a new subscription.
        """
        with transaction() as cur:
            cur.execute(
                "DELETE FROM idempotency_keys WHERE operation_id = ?",
                (operation_id,),
            )
