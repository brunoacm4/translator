# coding: utf-8

"""
In-memory Subscription Store

Tracks the mapping between 3GPP subscriptions and SM slice IDs.
Every ``AsSessionWithQoSSubscription`` that the translator creates
gets a ``SubscriptionRecord`` stored here so the CRUD endpoints
(GET / PUT / PATCH / DELETE) can retrieve it.

For production at Porto de Aveiro, this can be upgraded to SQLite or
PostgreSQL without changing the interface — just swap the implementation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SubscriptionRecord:
    """One stored subscription."""
    subscription_id: str
    scs_as_id: str
    sm_slice_id: str               # UUID the translator sent to SM create_slice
    imsi: str                      # Resolved IMSI used in SM associate
    subscription_data: Dict[str, Any]   # Full subscription JSON (for GET responses)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SubscriptionStore:
    """
    Thread-safe in-memory subscription store.

    Data structure: ``{ scsAsId: { subscriptionId: SubscriptionRecord } }``
    """

    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, SubscriptionRecord]] = {}

    # ── CRUD ──────────────────────────────────────────────────────────

    def create(
        self,
        scs_as_id: str,
        subscription_id: str,
        sm_slice_id: str,
        imsi: str,
        data: Dict[str, Any],
    ) -> SubscriptionRecord:
        """Create and store a new subscription record."""
        record = SubscriptionRecord(
            subscription_id=subscription_id,
            scs_as_id=scs_as_id,
            sm_slice_id=sm_slice_id,
            imsi=imsi,
            subscription_data=data,
        )
        self._data.setdefault(scs_as_id, {})[subscription_id] = record
        logger.info(
            "Store: created  scs=%s  sub=%s  slice=%s",
            scs_as_id, subscription_id, sm_slice_id,
        )
        return record

    def get(self, scs_as_id: str, subscription_id: str) -> Optional[SubscriptionRecord]:
        """Return a single subscription or None."""
        return self._data.get(scs_as_id, {}).get(subscription_id)

    def list_all(
        self,
        scs_as_id: str,
        ip_addrs: Optional[List[str]] = None,
    ) -> List[SubscriptionRecord]:
        """
        Return all subscriptions for an SCS/AS, optionally filtered by UE IP.
        """
        records = list(self._data.get(scs_as_id, {}).values())
        if ip_addrs:
            ip_set = set(ip_addrs)
            records = [
                r for r in records
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
        """Replace the subscription data for an existing record."""
        record = self.get(scs_as_id, subscription_id)
        if record is None:
            return None
        record.subscription_data = data
        record.updated_at = datetime.now(timezone.utc)
        logger.info("Store: updated  scs=%s  sub=%s", scs_as_id, subscription_id)
        return record

    def delete(self, scs_as_id: str, subscription_id: str) -> Optional[SubscriptionRecord]:
        """Remove and return a subscription record, or None if not found."""
        bucket = self._data.get(scs_as_id, {})
        record = bucket.pop(subscription_id, None)
        if record:
            logger.info("Store: deleted  scs=%s  sub=%s", scs_as_id, subscription_id)
        return record

    def get_by_sm_slice_id(self, sm_slice_id: str) -> Optional[SubscriptionRecord]:
        """Reverse lookup: find the subscription that owns a given SM slice ID."""
        for bucket in self._data.values():
            for record in bucket.values():
                if record.sm_slice_id == sm_slice_id:
                    return record
        return None

    @property
    def count(self) -> int:
        """Total number of stored subscriptions."""
        return sum(len(b) for b in self._data.values())


# ── Singleton ──────────────────────────────────────────────────────────
# One store instance shared across the entire application.
store = SubscriptionStore()
