# coding: utf-8

"""
    Translator Service — TS 29.122 AsSessionWithQoS

    Called by the 29.122 router (``app.apis.translator_api``). Maps 3GPP TS 29.122 AsSessionWithQoS subscription operations onto the
    translation core (``app.impl.session_core``), which drives the Slice
    Manager (model B: the SM pre-creates the slices, docs/adr/0004):

      POST   subscription  →  core start  (SM associate_slice)
      PUT    subscription  →  core change (SM change_slice when the slice changes)
      PATCH  subscription  →  core change (partial)
      DELETE subscription  →  core stop   (SM dissociate_slice)
      GET    subscription  →  read from SQLite-backed store

    One session per UE: a create for a UE that already has a session (in
    either API) is rejected with ``UE_SESSION_EXISTS_STATUS`` (docs/adr/0006).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Union

from fastapi import HTTPException
from pydantic import ValidationError

from app.impl.session_core import (
    QosIntent,
    SessionCore,
    UeSessionExists,
    bitrate_kbps,
    resolve_qos,
)
from app.models.nef.subscription import (
    AsSessionWithQoSSubscription,
    AsSessionWithQoSSubscriptionPatch,
)
from app.models.operation import OperationAccepted, OperationStatus
from app.store.subscription_store import store

# Config & utils
from app.config.subscriber_map import resolve_imsi
from app.utils.idempotency import build_payload_fingerprint
from app.utils.merge_patch import apply_merge_patch
from app.store.repositories import IdempotencyRepository

logger = logging.getLogger(__name__)

# docs/adr/0006 (decision 3): 409 if TS 29.122 allows it on a create, 403 if
# not. Kept at 403 until the TS 29.122 OpenAPI has been checked.
UE_SESSION_EXISTS_STATUS = 403


class TranslatorService:
    """
    Translates 3GPP TS 29.122 subscriptions into translation-core sessions.
    """

    def __init__(self) -> None:
        self.core = SessionCore()
        self.operation_repo = self.core.operation_repo
        self.idempotency_repo = IdempotencyRepository()

    # ================================================================== #
    #  Internal: 29.122 subscription → interface-neutral QoS intent       #
    # ================================================================== #
    @staticmethod
    def _intent(body: AsSessionWithQoSSubscription) -> QosIntent:
        """
        Map the QoS members of a subscription onto a ``QosIntent``.

        ``qosReference`` selects the slice; ``snssai`` and ``dnn`` must match
        it. ``tscQosReq`` is parsed (``req5Gsdelay`` → latency, ``priority``
        → priority, ``reqGbr*`` / ``reqMbr*`` → per-UE bitrates) but not
        applied until decision D3 (docs/adr/0006).
        """
        tsc = body.tscQosReq
        return QosIntent(
            qos_reference=body.qosReference,
            sst=body.snssai.sst if body.snssai else None,
            sd=body.snssai.sd if body.snssai else None,
            dnn=body.dnn,
            latency_ms=tsc.req5Gsdelay if tsc else None,
            priority=tsc.priority if tsc else None,
            gbr_dl_kbps=bitrate_kbps(tsc.reqGbrDl) if tsc else None,
            gbr_ul_kbps=bitrate_kbps(tsc.reqGbrUl) if tsc else None,
            mbr_dl_kbps=bitrate_kbps(tsc.reqMbrDl) if tsc else None,
            mbr_ul_kbps=bitrate_kbps(tsc.reqMbrUl) if tsc else None,
        )

    # ================================================================== #
    #  Internal: build self-link URL                                      #
    # ================================================================== #
    @staticmethod
    def _self_link(scs_as_id: str, subscription_id: str) -> str:
        return f"/3gpp-as-session-with-qos/v1/{scs_as_id}/subscriptions/{subscription_id}"

    # ================================================================== #
    #  Internal: serialize subscription for response / storage            #
    # ================================================================== #
    def _sub_to_dict(
        self,
        body: AsSessionWithQoSSubscription,
        scs_as_id: str,
        subscription_id: str,
    ) -> Dict[str, Any]:
        """
        Produce a dict of the subscription suitable for both storage and
        JSON response.  Sets the ``self`` link.
        """
        data = body.model_dump(by_alias=True, exclude_none=True)
        data["self"] = self._self_link(scs_as_id, subscription_id)
        return data

    # ------------------------------------------------------------------ #
    #  POST  — Create subscription                                        #
    # ------------------------------------------------------------------ #
    async def create_subscription(
        self,
        scs_as_id: str,
        body: AsSessionWithQoSSubscription,
        idempotency_key: Optional[str] = None,
    ) -> Union[AsSessionWithQoSSubscription, OperationAccepted]:
        """
        Full create flow with idempotency guard:
          1. Reserve idempotency key/fingerprint
          2. Resolve QoS profile and IMSI
          3. Reject a second session for the UE (docs/adr/0006)
          4. Start the session in the translation core (SM associate)
          5. Store subscription and operation status
        """

        body_for_fingerprint = body.model_dump(by_alias=True, exclude_none=True)
        payload_fingerprint = build_payload_fingerprint(scs_as_id, body_for_fingerprint)
        operation_id = str(uuid.uuid4())

        idem_result = self.idempotency_repo.reserve_or_get_existing(
            scs_as_id=scs_as_id,
            idempotency_key=idempotency_key,
            payload_fingerprint=payload_fingerprint,
            operation_id=operation_id,
        )
        if not idem_result["reserved"]:
            return OperationAccepted(
                operationId=idem_result["operation_id"],
                status=idem_result["status"],
                subscriptionId=idem_result.get("subscription_id"),
            )

        try:
            return await self._create_reserved(scs_as_id, body, operation_id)
        except Exception:
            # The create did not stand: let an identical request try again.
            self.idempotency_repo.release(operation_id)
            raise

    async def _create_reserved(
        self,
        scs_as_id: str,
        body: AsSessionWithQoSSubscription,
        operation_id: str,
    ) -> AsSessionWithQoSSubscription:
        """Create flow once the idempotency key/fingerprint is reserved."""
        self.operation_repo.create(operation_id=operation_id, scs_as_id=scs_as_id, status="pending")

        # -- 1. QoS profile, S-NSSAI, DNN -------------------------------------
        try:
            resolved = resolve_qos(self._intent(body))
        except ValueError as exc:
            self.operation_repo.update_status(
                operation_id=operation_id,
                status="failed",
                error=str(exc),
            )
            raise HTTPException(status_code=400, detail=str(exc))

        # -- 2. IMSI resolution ----------------------------------------------
        try:
            imsi = resolve_imsi(ue_ipv4=body.ueIpv4Addr, ue_ipv6=body.ueIpv6Addr)
        except ValueError as exc:
            self.operation_repo.update_status(
                operation_id=operation_id,
                status="failed",
                error=str(exc),
            )
            raise HTTPException(status_code=400, detail=str(exc))

        # -- 3-5. One session per UE; SM associate; store -------------------
        subscription_id = str(uuid.uuid4())
        sub_data = self._sub_to_dict(body, scs_as_id, subscription_id)
        async with self.core.ue_lock(imsi):
            try:
                self.core.ensure_no_session(imsi)
            except UeSessionExists as exc:
                self.operation_repo.update_status(
                    operation_id=operation_id, status="failed", error=str(exc)
                )
                raise HTTPException(status_code=UE_SESSION_EXISTS_STATUS, detail=str(exc))

            sm_request_id = await self.core.start(
                owner=scs_as_id,
                session_id=subscription_id,
                resolved=resolved,
                imsi=imsi,
                operation_id=operation_id,
            )
            store.create(
                scs_as_id=scs_as_id,
                subscription_id=subscription_id,
                sm_slice_id=resolved.slice_id,
                imsi=imsi,
                data=sub_data,
                operation_id=operation_id,
            )

        self.core.track(
            operation_id=operation_id,
            session_id=subscription_id,
            slice_id=resolved.slice_id,
            sm_request_id=sm_request_id,
        )

        # -- 6. Return -------------------------------------------------------
        return AsSessionWithQoSSubscription.model_validate(sub_data)

    # ------------------------------------------------------------------ #
    #  GET list — List subscriptions                                      #
    # ------------------------------------------------------------------ #
    async def list_subscriptions(
        self,
        scs_as_id: str,
        ip_addrs: Optional[List[str]] = None,
    ) -> List[AsSessionWithQoSSubscription]:
        records = store.list_all(scs_as_id, ip_addrs)
        return [
            AsSessionWithQoSSubscription.model_validate(r.subscription_data)
            for r in records
        ]

    # ------------------------------------------------------------------ #
    #  GET one — Get subscription                                         #
    # ------------------------------------------------------------------ #
    async def get_subscription(
        self,
        scs_as_id: str,
        subscription_id: str,
    ) -> AsSessionWithQoSSubscription:
        record = store.get(scs_as_id, subscription_id)
        if not record:
            raise HTTPException(status_code=404, detail="Subscription not found")
        return AsSessionWithQoSSubscription.model_validate(record.subscription_data)

    # ------------------------------------------------------------------ #
    #  PUT — Full replace                                                 #
    # ------------------------------------------------------------------ #
    async def update_subscription(
        self,
        scs_as_id: str,
        subscription_id: str,
        body: AsSessionWithQoSSubscription,
    ) -> AsSessionWithQoSSubscription:
        """
        Full replace: resolve the new QoS, move the UE when its slice changes
        (tracked and notified, docs/adr/0006), update the store.
        """
        record = store.get(scs_as_id, subscription_id)
        if not record:
            raise HTTPException(status_code=404, detail="Subscription not found")

        # The UE cannot change: the session lives on this UE's IMSI at the SM
        # (for the same reason the 3GPP PATCH body has no UE address fields).
        try:
            imsi = resolve_imsi(ue_ipv4=body.ueIpv4Addr, ue_ipv6=body.ueIpv6Addr)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if imsi != record.imsi:
            raise HTTPException(
                status_code=400,
                detail=(
                    "The UE of a subscription cannot be changed; "
                    "delete the subscription and create a new one"
                ),
            )

        try:
            resolved = resolve_qos(self._intent(body))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        slice_id = await self.core.change(
            owner=scs_as_id,
            session_id=subscription_id,
            imsi=record.imsi,
            current_slice_id=record.sm_slice_id,
            resolved=resolved,
            description=f"(PUT)  sub={subscription_id}",
        )

        # Update store
        sub_data = self._sub_to_dict(body, scs_as_id, subscription_id)
        store.update(scs_as_id, subscription_id, sub_data, sm_slice_id=slice_id)

        return AsSessionWithQoSSubscription.model_validate(sub_data)

    # ------------------------------------------------------------------ #
    #  PATCH — Partial update                                             #
    # ------------------------------------------------------------------ #
    async def patch_subscription(
        self,
        scs_as_id: str,
        subscription_id: str,
        body: AsSessionWithQoSSubscriptionPatch,
    ) -> AsSessionWithQoSSubscription:
        """
        JSON merge patch (RFC 7396) onto the existing subscription, then
        change the session in the core if QoS-relevant fields changed.
        """
        record = store.get(scs_as_id, subscription_id)
        if not record:
            raise HTTPException(status_code=404, detail="Subscription not found")

        # Only the members the client sent; an explicit null removes a member.
        patch_data = body.model_dump(mode="json", exclude_unset=True)
        existing = apply_merge_patch(record.subscription_data, patch_data)
        try:
            merged_sub = AsSessionWithQoSSubscription.model_validate(existing)
        except ValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"The patch leaves the subscription invalid: {exc.errors()[0]['msg']}",
            )

        # Determine if we need to call SM
        slice_id: Optional[str] = None
        if "qosReference" in patch_data or "tscQosReq" in patch_data:
            try:
                resolved = resolve_qos(self._intent(merged_sub))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

            slice_id = await self.core.change(
                owner=scs_as_id,
                session_id=subscription_id,
                imsi=record.imsi,
                current_slice_id=record.sm_slice_id,
                resolved=resolved,
                description=f"(PATCH)  sub={subscription_id}",
            )

        # Update store
        existing["self"] = self._self_link(scs_as_id, subscription_id)
        store.update(scs_as_id, subscription_id, existing, sm_slice_id=slice_id)

        return AsSessionWithQoSSubscription.model_validate(existing)

    # ------------------------------------------------------------------ #
    #  DELETE — Delete subscription                                       #
    # ------------------------------------------------------------------ #
    async def delete_subscription(
        self,
        scs_as_id: str,
        subscription_id: str,
    ) -> None:
        """Stop the session in the core (dissociate the UE) and forget the
        subscription."""
        record = store.get(scs_as_id, subscription_id)
        if not record:
            raise HTTPException(status_code=404, detail="Subscription not found")

        await self.core.stop(
            imsi=record.imsi,
            slice_id=record.sm_slice_id,
            session_id=subscription_id,
        )

        store.delete(scs_as_id, subscription_id)
        # An identical create after this delete must start a new subscription.
        if record.operation_id:
            self.idempotency_repo.release(record.operation_id)

    # ------------------------------------------------------------------ #
    #  GET /operations/{operationId}                                      #
    # ------------------------------------------------------------------ #
    async def get_operation(self, operation_id: str) -> OperationStatus:
        """Return the current status of a previously submitted operation."""
        record = self.operation_repo.get(operation_id)
        if not record:
            raise HTTPException(status_code=404, detail="Operation not found")
        return OperationStatus(
            operationId=record["operation_id"],
            status=record["status"],
            subscriptionId=record.get("subscription_id"),
            smSliceId=record.get("sm_slice_id"),
            smRequestIds=[record["sm_request_id"]] if record.get("sm_request_id") else [],
            error=record.get("error"),
            createdAt=record.get("created_at"),
            updatedAt=record.get("updated_at"),
        )
