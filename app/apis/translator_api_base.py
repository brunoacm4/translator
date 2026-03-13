# coding: utf-8

"""
    Translator API — Base (Abstract)

    Defines the abstract contract for the translator service implementation.
    Concrete subclasses are auto-registered via __init_subclass__ and
    discovered at startup by the router module (translator_api.py).

    Method signatures mirror the 3GPP TS 29.122 AsSessionWithQoS operations.
"""

from __future__ import annotations

from typing import ClassVar, Dict, List, Optional, Tuple

from app.models.nef.subscription import (
    AsSessionWithQoSSubscription,
    AsSessionWithQoSSubscriptionPatch,
)


class BaseTranslatorApi:
    subclasses: ClassVar[Tuple] = ()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        BaseTranslatorApi.subclasses = BaseTranslatorApi.subclasses + (cls,)

    async def create_subscription(
        self,
        scs_as_id: str,
        body: AsSessionWithQoSSubscription,
    ) -> AsSessionWithQoSSubscription:
        """POST /{scsAsId}/subscriptions → SM create + associate."""
        ...

    async def list_subscriptions(
        self,
        scs_as_id: str,
        ip_addrs: Optional[List[str]] = None,
    ) -> List[AsSessionWithQoSSubscription]:
        """GET /{scsAsId}/subscriptions."""
        ...

    async def get_subscription(
        self,
        scs_as_id: str,
        subscription_id: str,
    ) -> AsSessionWithQoSSubscription:
        """GET /{scsAsId}/subscriptions/{subscriptionId}."""
        ...

    async def update_subscription(
        self,
        scs_as_id: str,
        subscription_id: str,
        body: AsSessionWithQoSSubscription,
    ) -> AsSessionWithQoSSubscription:
        """PUT /{scsAsId}/subscriptions/{subscriptionId} → SM change."""
        ...

    async def patch_subscription(
        self,
        scs_as_id: str,
        subscription_id: str,
        body: AsSessionWithQoSSubscriptionPatch,
    ) -> AsSessionWithQoSSubscription:
        """PATCH /{scsAsId}/subscriptions/{subscriptionId} → SM change."""
        ...

    async def delete_subscription(
        self,
        scs_as_id: str,
        subscription_id: str,
    ) -> None:
        """DELETE /{scsAsId}/subscriptions/{subscriptionId} → SM delete."""
        ...
