# coding: utf-8

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db.connection import get_connection, transaction


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SubscriptionRepository:
    def create(
        self,
        *,
        scs_as_id: str,
        subscription_id: str,
        sm_slice_id: str,
        imsi: str,
        operation_id: Optional[str],
        data: Dict[str, Any],
    ) -> None:
        now = _now_iso()
        with transaction() as cur:
            cur.execute(
                """
                INSERT INTO subscriptions (
                    scs_as_id,
                    subscription_id,
                    sm_slice_id,
                    imsi,
                    operation_id,
                    subscription_data,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (scs_as_id, subscription_id) DO UPDATE SET
                    sm_slice_id=excluded.sm_slice_id,
                    imsi=excluded.imsi,
                    operation_id=excluded.operation_id,
                    subscription_data=excluded.subscription_data,
                    updated_at=excluded.updated_at
                """,
                (
                    scs_as_id,
                    subscription_id,
                    sm_slice_id,
                    imsi,
                    operation_id,
                    json.dumps(data),
                    now,
                    now,
                ),
            )

    def get(self, scs_as_id: str, subscription_id: str) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        row = conn.execute(
            """
            SELECT scs_as_id, subscription_id, sm_slice_id, imsi, operation_id,
                   subscription_data, created_at, updated_at
            FROM subscriptions
            WHERE scs_as_id = ? AND subscription_id = ?
            """,
            (scs_as_id, subscription_id),
        ).fetchone()
        return self._to_dict(row)

    def list_all(self, scs_as_id: str) -> List[Dict[str, Any]]:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT scs_as_id, subscription_id, sm_slice_id, imsi, operation_id,
                   subscription_data, created_at, updated_at
            FROM subscriptions
            WHERE scs_as_id = ?
            ORDER BY created_at
            """,
            (scs_as_id,),
        ).fetchall()
        return [self._to_dict(r) for r in rows if r is not None]

    def update(self, scs_as_id: str, subscription_id: str, data: Dict[str, Any]) -> bool:
        now = _now_iso()
        with transaction() as cur:
            cur.execute(
                """
                UPDATE subscriptions
                SET subscription_data = ?, updated_at = ?
                WHERE scs_as_id = ? AND subscription_id = ?
                """,
                (json.dumps(data), now, scs_as_id, subscription_id),
            )
            return cur.rowcount > 0

    def delete(self, scs_as_id: str, subscription_id: str) -> bool:
        with transaction() as cur:
            cur.execute(
                "DELETE FROM subscriptions WHERE scs_as_id = ? AND subscription_id = ?",
                (scs_as_id, subscription_id),
            )
            return cur.rowcount > 0

    def get_by_sm_slice_id(self, sm_slice_id: str) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        row = conn.execute(
            """
            SELECT scs_as_id, subscription_id, sm_slice_id, imsi, operation_id,
                   subscription_data, created_at, updated_at
            FROM subscriptions
            WHERE sm_slice_id = ?
            LIMIT 1
            """,
            (sm_slice_id,),
        ).fetchone()
        return self._to_dict(row)

    def count(self) -> int:
        conn = get_connection()
        row = conn.execute("SELECT COUNT(*) AS c FROM subscriptions").fetchone()
        return int(row["c"]) if row else 0

    @staticmethod
    def _to_dict(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return {
            "scs_as_id": row["scs_as_id"],
            "subscription_id": row["subscription_id"],
            "sm_slice_id": row["sm_slice_id"],
            "imsi": row["imsi"],
            "operation_id": row["operation_id"],
            "subscription_data": json.loads(row["subscription_data"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


class OperationRepository:
    def create(
        self,
        *,
        operation_id: str,
        scs_as_id: str,
        idempotency_key: Optional[str],
        payload_fingerprint: str,
        status: str,
    ) -> None:
        now = _now_iso()
        with transaction() as cur:
            cur.execute(
                """
                INSERT INTO operations (
                    operation_id,
                    scs_as_id,
                    idempotency_key,
                    payload_fingerprint,
                    status,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    scs_as_id,
                    idempotency_key,
                    payload_fingerprint,
                    status,
                    now,
                    now,
                ),
            )

    def get(self, operation_id: str) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        row = conn.execute(
            """
            SELECT operation_id, scs_as_id, idempotency_key, payload_fingerprint,
                   status, subscription_id, sm_slice_id, sm_request_id,
                   notification_url, error, created_at, updated_at
            FROM operations
            WHERE operation_id = ?
            """,
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
        notification_url: Optional[str] = None,
        error: Optional[str] = None,
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
                    notification_url = COALESCE(?, notification_url),
                    error = COALESCE(?, error),
                    updated_at = ?
                WHERE operation_id = ?
                """,
                (
                    status,
                    subscription_id,
                    sm_slice_id,
                    sm_request_id,
                    notification_url,
                    error,
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
            """
            SELECT operation_id, scs_as_id, idempotency_key, payload_fingerprint,
                   status, subscription_id, sm_slice_id, sm_request_id,
                   notification_url, error, created_at, updated_at
            FROM operations
            WHERE status = 'sm_provisioning'
              AND sm_request_id IS NOT NULL
            """,
        ).fetchall()
        return [self._to_dict(r) for r in rows if r]

    @staticmethod
    def _to_dict(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return {
            "operation_id": row["operation_id"],
            "scs_as_id": row["scs_as_id"],
            "idempotency_key": row["idempotency_key"],
            "payload_fingerprint": row["payload_fingerprint"],
            "status": row["status"],
            "subscription_id": row["subscription_id"],
            "sm_slice_id": row["sm_slice_id"],
            "sm_request_id": row["sm_request_id"],
            "notification_url": row["notification_url"],
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


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


class SliceRegistryRepository:
    """
    Tracks which SM slices exist and how many subscriptions reference each one.

    Primary key: (snssai, dnn) — one SM slice per unique SNSSAI+DNN pair.
    ``ref_count`` is incremented when a UE is associated and decremented on
    dissociation; when it reaches 0 the slice is deleted from the SM.
    """

    def get(self, snssai: str, dnn: str) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        row = conn.execute(
            "SELECT snssai, dnn, sm_slice_id, ref_count FROM slice_registry "
            "WHERE snssai = ? AND dnn = ?",
            (snssai, dnn),
        ).fetchone()
        return self._to_dict(row)

    def get_by_slice_id(self, sm_slice_id: str) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        row = conn.execute(
            "SELECT snssai, dnn, sm_slice_id, ref_count FROM slice_registry "
            "WHERE sm_slice_id = ?",
            (sm_slice_id,),
        ).fetchone()
        return self._to_dict(row)

    def get_or_create(
        self, snssai: str, dnn: str, sm_slice_id: str
    ) -> tuple[Dict[str, Any], bool]:
        """
        Atomically return an existing registry entry or insert a new one
        with ``ref_count=0``.

        Returns:
            (entry_dict, created) where ``created`` is True when a new row
            was inserted, False when an existing one was returned.
        """
        with transaction() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO slice_registry (snssai, dnn, sm_slice_id, ref_count) "
                "VALUES (?, ?, ?, 0)",
                (snssai, dnn, sm_slice_id),
            )
            created = cur.rowcount > 0
        row = get_connection().execute(
            "SELECT snssai, dnn, sm_slice_id, ref_count FROM slice_registry "
            "WHERE snssai = ? AND dnn = ?",
            (snssai, dnn),
        ).fetchone()
        return self._to_dict(row), created  # type: ignore[return-value]

    def increment_ref(self, snssai: str, dnn: str) -> int:
        """Increment ref_count and return the new value."""
        with transaction() as cur:
            cur.execute(
                "UPDATE slice_registry SET ref_count = ref_count + 1 "
                "WHERE snssai = ? AND dnn = ?",
                (snssai, dnn),
            )
        row = get_connection().execute(
            "SELECT ref_count FROM slice_registry WHERE snssai = ? AND dnn = ?",
            (snssai, dnn),
        ).fetchone()
        return int(row["ref_count"]) if row else 0

    def decrement_ref(self, snssai: str, dnn: str) -> int:
        """Decrement ref_count (floor 0) and return the new value."""
        with transaction() as cur:
            cur.execute(
                "UPDATE slice_registry SET ref_count = MAX(0, ref_count - 1) "
                "WHERE snssai = ? AND dnn = ?",
                (snssai, dnn),
            )
        row = get_connection().execute(
            "SELECT ref_count FROM slice_registry WHERE snssai = ? AND dnn = ?",
            (snssai, dnn),
        ).fetchone()
        return int(row["ref_count"]) if row else 0

    def delete(self, snssai: str, dnn: str) -> None:
        with transaction() as cur:
            cur.execute(
                "DELETE FROM slice_registry WHERE snssai = ? AND dnn = ?",
                (snssai, dnn),
            )

    @staticmethod
    def _to_dict(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return {
            "snssai": row["snssai"],
            "dnn": row["dnn"],
            "sm_slice_id": row["sm_slice_id"],
            "ref_count": row["ref_count"],
        }
