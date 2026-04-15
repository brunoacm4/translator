# coding: utf-8

"""
SQLite-backed Subscription Store

Tracks the mapping between 3GPP subscriptions and SM slice IDs.
Preserves the previous store interface so service logic remains stable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.store.repositories import SubscriptionRepository

logger = logging.getLogger(__name__)


@dataclass
class SubscriptionRecord:
    """One stored subscription."""

    subscription_id: str
    scs_as_id: str
    sm_slice_id: str
    imsi: str
    subscription_data: Dict[str, Any]
    operation_id: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SubscriptionStore:
    """SQLite-backed subscription store with the legacy interface."""

    def __init__(self) -> None:
        self._repo = SubscriptionRepository()

    def create(
        self,
        scs_as_id: str,
        subscription_id: str,
        sm_slice_id: str,
        imsi: str,
        data: Dict[str, Any],
        operation_id: Optional[str] = None,
    ) -> SubscriptionRecord:
        self._repo.create(
            scs_as_id=scs_as_id,
            subscription_id=subscription_id,
            sm_slice_id=sm_slice_id,
            imsi=imsi,
            operation_id=operation_id,
            data=data,
        )
        logger.info(
            "Store: created  scs=%s  sub=%s  slice=%s",
            scs_as_id,
            subscription_id,
            sm_slice_id,
        )
        record = self.get(scs_as_id, subscription_id)
        if record is None:
            raise RuntimeError("Failed to read persisted subscription after create")
        return record

    def get(self, scs_as_id: str, subscription_id: str) -> Optional[SubscriptionRecord]:
        row = self._repo.get(scs_as_id, subscription_id)
        return self._to_record(row)

    def list_all(
        self,
        scs_as_id: str,
        ip_addrs: Optional[List[str]] = None,
    ) -> List[SubscriptionRecord]:
        rows = self._repo.list_all(scs_as_id)
        records = [self._to_record(r) for r in rows if r is not None]
        if ip_addrs:
            ip_set = set(ip_addrs)
            records = [
                r
                for r in records
                if r.subscription_data.get("ueIpv4Addr") in ip_set
                or r.subscription_data.get("ueIpv6Addr") in ip_set
            ]
        return records

    def update(
        self,
        scs_as_id: str,
        subscription_id: str,
        data: Dict[str, Any],
    ) -> Optional[SubscriptionRecord]:
        updated = self._repo.update(scs_as_id, subscription_id, data)
        if not updated:
            return None
        logger.info("Store: updated  scs=%s  sub=%s", scs_as_id, subscription_id)
        return self.get(scs_as_id, subscription_id)

    def delete(self, scs_as_id: str, subscription_id: str) -> Optional[SubscriptionRecord]:
        existing = self.get(scs_as_id, subscription_id)
        deleted = self._repo.delete(scs_as_id, subscription_id)
        if deleted:
            logger.info("Store: deleted  scs=%s  sub=%s", scs_as_id, subscription_id)
            return existing
        return None

    def get_by_sm_slice_id(self, sm_slice_id: str) -> Optional[SubscriptionRecord]:
        row = self._repo.get_by_sm_slice_id(sm_slice_id)
        return self._to_record(row)

    @property
    def count(self) -> int:
        return self._repo.count()

    @staticmethod
    def _to_record(row: Optional[Dict[str, Any]]) -> Optional[SubscriptionRecord]:
        if row is None:
            return None
        return SubscriptionRecord(
            subscription_id=row["subscription_id"],
            scs_as_id=row["scs_as_id"],
            sm_slice_id=row["sm_slice_id"],
            imsi=row["imsi"],
            operation_id=row.get("operation_id"),
            subscription_data=row["subscription_data"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


store = SubscriptionStore()
