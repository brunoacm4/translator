# coding: utf-8

"""
SQLite-backed session store.

Maps each 3GPP session (a 29.122 subscription or an N5 application session)
to the UE's IMSI and the SM slice the UE is on. Both APIs use the module-level
``store``; N5 sessions sit under the reserved owner ``N5_OWNER``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db.connection import get_connection, transaction

logger = logging.getLogger(__name__)

_COLUMNS = "scs_as_id, subscription_id, sm_slice_id, imsi, operation_id, subscription_data"


@dataclass
class SubscriptionRecord:
    """One stored session."""

    subscription_id: str
    scs_as_id: str
    sm_slice_id: str
    imsi: str
    subscription_data: Dict[str, Any]
    operation_id: Optional[str] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_record(row: Optional[sqlite3.Row]) -> Optional[SubscriptionRecord]:
    if row is None:
        return None
    return SubscriptionRecord(
        subscription_id=row["subscription_id"],
        scs_as_id=row["scs_as_id"],
        sm_slice_id=row["sm_slice_id"],
        imsi=row["imsi"],
        operation_id=row["operation_id"],
        subscription_data=json.loads(row["subscription_data"]),
    )


class SubscriptionStore:
    """The ``subscriptions`` table."""

    def create(
        self,
        scs_as_id: str,
        subscription_id: str,
        sm_slice_id: str,
        imsi: str,
        data: Dict[str, Any],
        operation_id: Optional[str] = None,
    ) -> None:
        now = _now_iso()
        with transaction() as cur:
            cur.execute(
                f"""
                INSERT INTO subscriptions ({_COLUMNS}, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (scs_as_id, subscription_id, sm_slice_id, imsi, operation_id,
                 json.dumps(data), now, now),
            )
        logger.info(
            "Store: created  scs=%s  sub=%s  slice=%s", scs_as_id, subscription_id, sm_slice_id
        )

    def get(self, scs_as_id: str, subscription_id: str) -> Optional[SubscriptionRecord]:
        row = get_connection().execute(
            f"SELECT {_COLUMNS} FROM subscriptions WHERE scs_as_id = ? AND subscription_id = ?",
            (scs_as_id, subscription_id),
        ).fetchone()
        return _to_record(row)

    def list_all(
        self,
        scs_as_id: str,
        ip_addrs: Optional[List[str]] = None,
    ) -> List[SubscriptionRecord]:
        """The owner's sessions, oldest first; with ``ip_addrs``, only those
        whose 29.122 ``ueIpv4Addr`` / ``ueIpv6Addr`` is in the list."""
        rows = get_connection().execute(
            f"SELECT {_COLUMNS} FROM subscriptions WHERE scs_as_id = ? ORDER BY created_at",
            (scs_as_id,),
        ).fetchall()
        records = [_to_record(r) for r in rows]
        if ip_addrs:
            wanted = set(ip_addrs)
            records = [
                r for r in records
                if r.subscription_data.get("ueIpv4Addr") in wanted
                or r.subscription_data.get("ueIpv6Addr") in wanted
            ]
        return records

    def get_by_imsi(self, imsi: str) -> Optional[SubscriptionRecord]:
        """Any session of the UE, in either API."""
        row = get_connection().execute(
            f"SELECT {_COLUMNS} FROM subscriptions WHERE imsi = ? LIMIT 1",
            (imsi,),
        ).fetchone()
        return _to_record(row)

    def update(
        self,
        scs_as_id: str,
        subscription_id: str,
        data: Dict[str, Any],
        sm_slice_id: Optional[str] = None,
    ) -> bool:
        """Replace the stored data and, when given, the session's slice."""
        with transaction() as cur:
            cur.execute(
                """
                UPDATE subscriptions
                SET subscription_data = ?, sm_slice_id = COALESCE(?, sm_slice_id), updated_at = ?
                WHERE scs_as_id = ? AND subscription_id = ?
                """,
                (json.dumps(data), sm_slice_id, _now_iso(), scs_as_id, subscription_id),
            )
            updated = cur.rowcount > 0
        if updated:
            logger.info(
                "Store: updated  scs=%s  sub=%s%s",
                scs_as_id, subscription_id, f"  slice={sm_slice_id}" if sm_slice_id else "",
            )
        return updated

    def restore_slice(
        self,
        scs_as_id: str,
        subscription_id: str,
        *,
        expected_slice_id: str,
        previous_slice_id: str,
    ) -> bool:
        """Put a session back on ``previous_slice_id`` after a failed QoS change.

        Only applies while the session is still on ``expected_slice_id``, so a
        later change is never undone.
        """
        with transaction() as cur:
            cur.execute(
                """
                UPDATE subscriptions
                SET sm_slice_id = ?, updated_at = ?
                WHERE scs_as_id = ? AND subscription_id = ? AND sm_slice_id = ?
                """,
                (previous_slice_id, _now_iso(), scs_as_id, subscription_id, expected_slice_id),
            )
            restored = cur.rowcount > 0
        if restored:
            logger.info(
                "Store: slice restored  scs=%s  sub=%s  slice=%s",
                scs_as_id, subscription_id, previous_slice_id,
            )
        return restored

    def delete(self, scs_as_id: str, subscription_id: str) -> bool:
        with transaction() as cur:
            cur.execute(
                "DELETE FROM subscriptions WHERE scs_as_id = ? AND subscription_id = ?",
                (scs_as_id, subscription_id),
            )
            deleted = cur.rowcount > 0
        if deleted:
            logger.info("Store: deleted  scs=%s  sub=%s", scs_as_id, subscription_id)
        return deleted

    @property
    def count(self) -> int:
        row = get_connection().execute("SELECT COUNT(*) AS c FROM subscriptions").fetchone()
        return int(row["c"]) if row else 0


store = SubscriptionStore()
