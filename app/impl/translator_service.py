# coding: utf-8

"""
    Translator Service — Implementation

    Concrete implementation of ``BaseTranslatorApi``.  Auto-registered at
    import time via ``__init_subclass__``.

    Translates 3GPP TS 29.122 AsSessionWithQoS subscription operations
    into IT Aveiro Slice Manager API calls:

      POST   subscription  →  SM create_slice + associate_slice
      PUT    subscription  →  SM change_slice
      PATCH  subscription  →  SM change_slice (partial)
      DELETE subscription  →  SM delete_slice
      GET    subscription  →  read from in-memory store
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.apis.translator_api_base import BaseTranslatorApi
from app.impl.sm_client import SliceManagerClient
from app.resilience.circuit_breaker import CircuitBreakerOpen
from app.models.nef.subscription import (
    AsSessionWithQoSSubscription,
    AsSessionWithQoSSubscriptionPatch,
)
from app.models.nef.common import Snssai
from app.store.subscription_store import store

# Config & utils
from app.config.qos_profiles import resolve_qos_profile, QoSProfile
from app.config.subscriber_map import resolve_imsi
from app.config import testbed_defaults as tb
from app.utils.converters import parse_bitrate_to_kbps

logger = logging.getLogger(__name__)


def _add_if_not_none(payload: Dict[str, Any], key: str, value: Any) -> None:
    """Add a key to the payload dict only when the value is not None."""
    if value is not None:
        payload[key] = value


class TranslatorService(BaseTranslatorApi):
    """
    Orchestrates the full 3GPP NEF → Slice Manager translation flow.
    """

    def __init__(self) -> None:
        self.sm_client = SliceManagerClient()

    # ================================================================== #
    #  Internal: SM call wrapper                                          #
    # ================================================================== #

    @staticmethod
    async def _call_sm(coro, description: str) -> None:
        """
        Await an SM coroutine, translating exceptions to HTTP status codes:
          - CircuitBreakerOpen → 503 (SM is down, try later)
          - Any other error    → 502 (SM returned/caused an error)
        """
        try:
            await coro
        except CircuitBreakerOpen as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"{description} failed: {exc}")

    # ================================================================== #
    #  Internal: resolve SNSSAI (from body snssai or QoS profile)         #
    # ================================================================== #
    @staticmethod
    def _resolve_snssai(
        body: AsSessionWithQoSSubscription,
        qos: Optional[QoSProfile],
    ) -> tuple[int, Optional[str], str]:
        """
        Determine SST, SD, and hex SNSSAI string.

        Priority: explicit ``body.snssai`` > QoS profile > testbed defaults.

        Returns:
            (sst, sd, snssai_hex)
        """
        if body.snssai:
            sst = body.snssai.sst
            sd = body.snssai.sd
        elif qos:
            sst = qos.sst
            sd = qos.sd
        else:
            sst = 1
            sd = "000001"

        snssai_hex = f"{sst:02X}{sd}" if sd else f"{sst:02X}"
        return sst, sd, snssai_hex

    # ================================================================== #
    #  Internal: build SM create_slice payload                            #
    # ================================================================== #
    def _build_create_payload(
        self,
        body: AsSessionWithQoSSubscription,
        slice_id: str,
        qos: Optional[QoSProfile],
        sst: int,
        sd: Optional[str],
        dnn: str,
    ) -> Dict[str, Any]:
        """
        Build the ``CoreSliceCreatePostRequest`` dict.

        Strategy:
          1. Start with QoS-profile defaults (if qosReference matched)
          2. Override with ``tscQosReq`` fields (BitRate strings → KBPS)
          3. Forward-if-present for all other SM fields
        """
        payload: Dict[str, Any] = {
            "id": slice_id,
            "sst": sst,
            "sd": sd or "",
            "dnn": dnn,
        }

        # Latency: tscQosReq.req5Gsdelay > QoS profile
        latency = None
        if body.tscQosReq and body.tscQosReq.req5Gsdelay is not None:
            latency = body.tscQosReq.req5Gsdelay
        elif qos:
            latency = qos.latency_ms
        if latency is not None:
            payload["dllatency"] = latency
            payload["ullatency"] = latency

        # Priority: tscQosReq.priority > QoS profile
        priority = None
        if body.tscQosReq and body.tscQosReq.priority is not None:
            priority = body.tscQosReq.priority
        elif qos:
            priority = qos.prioritylabel
        _add_if_not_none(payload, "prioritylabel", priority)

        # Reliability: from QoS profile (no 3GPP equivalent in tscQosReq)
        if qos:
            payload["reliability"] = qos.reliability_percent

        # Delay tolerance, deterministic comm, mobility: from QoS profile
        if qos:
            payload["delaytolerance"] = qos.delaytolerance
            payload["dldeterministiccomm"] = qos.dldeterministiccomm
            payload["uldeterministiccomm"] = qos.uldeterministiccomm
            payload["uemobilitylevel"] = qos.uemobilitylevel

        # Throughput from tscQosReq BitRate strings
        if body.tscQosReq:
            if body.tscQosReq.reqGbrDl:
                kbps = parse_bitrate_to_kbps(body.tscQosReq.reqGbrDl)
                _add_if_not_none(payload, "dlguathptperue", kbps)
            if body.tscQosReq.reqGbrUl:
                kbps = parse_bitrate_to_kbps(body.tscQosReq.reqGbrUl)
                _add_if_not_none(payload, "ulguathptperue", kbps)
            if body.tscQosReq.reqMbrDl:
                kbps = parse_bitrate_to_kbps(body.tscQosReq.reqMbrDl)
                _add_if_not_none(payload, "dlmaxthptperue", kbps)
            if body.tscQosReq.reqMbrUl:
                kbps = parse_bitrate_to_kbps(body.tscQosReq.reqMbrUl)
                _add_if_not_none(payload, "ulmaxthptperue", kbps)

        return payload

    # ================================================================== #
    #  Internal: build SM associate_slice payload                         #
    # ================================================================== #
    def _build_associate_payload(
        self,
        body: AsSessionWithQoSSubscription,
        slice_id: str,
        imsi: str,
        snssai_hex: str,
        dnn: str,
    ) -> Dict[str, Any]:
        """
        Build the ``CoreSliceAssociatePostRequest`` dict.

        Uses testbed defaults for required fields the NEF doesn't provide.
        """
        return {
            "imsi": imsi,
            "slice": slice_id,
            "numIMSIs": tb.NUM_IMSIS,
            "ipv4": body.ueIpv4Addr or tb.DEFAULT_IPV4,
            "ipv6": body.ueIpv6Addr or tb.DEFAULT_IPV6,
            "amdata": tb.DEFAULT_AMDATA,
            "default": tb.DEFAULT_SLICE_FLAG,
            "uecanSendSNSSAI": tb.UE_CAN_SEND_SNSSAI,
            "ambrup": tb.DEFAULT_AMBR_UP,
            "ambrdw": tb.DEFAULT_AMBR_DW,
            "upUnit": tb.DEFAULT_UP_UNIT,
            "dwUnit": tb.DEFAULT_DW_UNIT,
            # Extra keys that ran_service.py reads as attributes
            "SNSSAI": snssai_hex,
            "DNN": dnn,
            "DNNQOSTPLID": tb.DEFAULT_DNN_QOS_TPL_ID,
            "DEFAULT": tb.DEFAULT_SLICE_FLAG,
        }

    # ================================================================== #
    #  Internal: build SM change_slice payload                            #
    # ================================================================== #
    def _build_change_payload(
        self,
        imsi: str,
        snssai_hex: str,
        dnn: str,
    ) -> Dict[str, Any]:
        """Build the ``CoreSliceChangePostRequest`` dict (all 4 fields required)."""
        return {
            "imsi": imsi,
            "snssai": snssai_hex,
            "dnn": dnn,
            "dnnqostplid": tb.DEFAULT_DNN_QOS_TPL_ID,
        }

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
    ) -> AsSessionWithQoSSubscription:
        """
        Full create flow:
          1. Resolve QoS profile from qosReference
          2. Resolve IMSI from ueIpv4Addr / ueIpv6Addr
          3. Determine SNSSAI (from body.snssai or QoS profile)
          4. Generate slice_id + subscription_id
          5. Build & send SM create_slice
          6. Build & send SM associate_slice (rollback on failure)
          7. Store subscription
          8. Return AsSessionWithQoSSubscription with self link
        """

        # -- 1. QoS profile -------------------------------------------------
        qos: Optional[QoSProfile] = None
        if body.qosReference:
            try:
                qos = resolve_qos_profile(body.qosReference)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

        # -- 2. IMSI resolution ----------------------------------------------
        try:
            imsi = resolve_imsi(ue_ipv4=body.ueIpv4Addr, ue_ipv6=body.ueIpv6Addr)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # -- 3. SNSSAI -------------------------------------------------------
        sst, sd, snssai_hex = self._resolve_snssai(body, qos)
        dnn = body.dnn or tb.DEFAULT_DNN

        # -- 4. IDs ----------------------------------------------------------
        slice_id = str(uuid.uuid4())
        subscription_id = str(uuid.uuid4())
        logger.info(
            "Creating subscription  scs=%s  sub=%s  slice=%s  imsi=%s",
            scs_as_id, subscription_id, slice_id, imsi,
        )

        # -- 5. SM create_slice ----------------------------------------------
        create_payload = self._build_create_payload(body, slice_id, qos, sst, sd, dnn)
        logger.info("→ SM create_slice  %s", create_payload)
        await self._call_sm(self.sm_client.create_slice(create_payload), "SM create_slice")

        # -- 6. SM associate_slice (with rollback) ---------------------------
        associate_payload = self._build_associate_payload(
            body, slice_id, imsi, snssai_hex, dnn,
        )
        logger.info("→ SM associate_slice  %s", associate_payload)
        try:
            await self.sm_client.associate_slice(associate_payload)
        except CircuitBreakerOpen as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception as exc:
            logger.error("SM associate_slice failed: %s — rolling back create", exc)
            try:
                await self.sm_client.delete_slice({"id": slice_id})
                logger.info("Rollback: deleted orphaned slice %s", slice_id)
            except Exception as rb_exc:
                logger.error("Rollback delete_slice also failed: %s", rb_exc)
            raise HTTPException(
                status_code=502,
                detail=f"SM associate_slice failed (create rolled back): {exc}",
            )

        # -- 7. Store subscription -------------------------------------------
        sub_data = self._sub_to_dict(body, scs_as_id, subscription_id)
        store.create(
            scs_as_id=scs_as_id,
            subscription_id=subscription_id,
            sm_slice_id=slice_id,
            imsi=imsi,
            data=sub_data,
        )

        # -- 8. Return -------------------------------------------------------
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
        Full replace: resolve new QoS, call SM change_slice, update store.
        """
        record = store.get(scs_as_id, subscription_id)
        if not record:
            raise HTTPException(status_code=404, detail="Subscription not found")

        # Resolve QoS
        qos: Optional[QoSProfile] = None
        if body.qosReference:
            try:
                qos = resolve_qos_profile(body.qosReference)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

        # SNSSAI
        _, _, snssai_hex = self._resolve_snssai(body, qos)
        dnn = body.dnn or tb.DEFAULT_DNN

        # SM change_slice
        change_payload = self._build_change_payload(record.imsi, snssai_hex, dnn)
        logger.info("→ SM change_slice (PUT)  sub=%s  %s", subscription_id, change_payload)
        await self._call_sm(self.sm_client.change_slice(change_payload), "SM change_slice")

        # Update store
        sub_data = self._sub_to_dict(body, scs_as_id, subscription_id)
        store.update(scs_as_id, subscription_id, sub_data)

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
        Merge-patch: overlay provided fields onto the existing subscription,
        then call SM change_slice if QoS-relevant fields changed.
        """
        record = store.get(scs_as_id, subscription_id)
        if not record:
            raise HTTPException(status_code=404, detail="Subscription not found")

        # Merge patch fields into existing data
        existing = dict(record.subscription_data)
        patch_data = body.model_dump(exclude_none=True)
        existing.update(patch_data)

        # Determine if we need to call SM
        needs_sm_change = "qosReference" in patch_data or "tscQosReq" in patch_data

        if needs_sm_change:
            # Reconstruct full subscription to resolve QoS
            merged_sub = AsSessionWithQoSSubscription.model_validate(existing)

            qos: Optional[QoSProfile] = None
            if merged_sub.qosReference:
                try:
                    qos = resolve_qos_profile(merged_sub.qosReference)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc))

            _, _, snssai_hex = self._resolve_snssai(merged_sub, qos)
            dnn = merged_sub.dnn or tb.DEFAULT_DNN

            change_payload = self._build_change_payload(record.imsi, snssai_hex, dnn)
            logger.info("→ SM change_slice (PATCH)  sub=%s  %s", subscription_id, change_payload)
            await self._call_sm(self.sm_client.change_slice(change_payload), "SM change_slice")

        # Update store
        existing["self"] = self._self_link(scs_as_id, subscription_id)
        store.update(scs_as_id, subscription_id, existing)

        return AsSessionWithQoSSubscription.model_validate(existing)

    # ------------------------------------------------------------------ #
    #  DELETE — Delete subscription                                       #
    # ------------------------------------------------------------------ #
    async def delete_subscription(
        self,
        scs_as_id: str,
        subscription_id: str,
    ) -> None:
        """Delete the subscription and the corresponding SM slice."""
        record = store.get(scs_as_id, subscription_id)
        if not record:
            raise HTTPException(status_code=404, detail="Subscription not found")

        # SM delete_slice
        delete_payload = {"id": record.sm_slice_id}
        logger.info("→ SM delete_slice  sub=%s  slice=%s", subscription_id, record.sm_slice_id)
        await self._call_sm(self.sm_client.delete_slice(delete_payload), "SM delete_slice")

        # Remove from store
        store.delete(scs_as_id, subscription_id)
