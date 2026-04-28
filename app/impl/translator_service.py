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
      GET    subscription  →  read from SQLite-backed store
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional, Union

import httpx
from fastapi import HTTPException

from app.apis.translator_api_base import BaseTranslatorApi
from app.impl.sm_client import SliceManagerClient
from app.resilience.circuit_breaker import CircuitBreakerOpen
from app.models.nef.subscription import (
    AsSessionWithQoSSubscription,
    AsSessionWithQoSSubscriptionPatch,
)
from app.models.operation import OperationAccepted
from app.store.subscription_store import store

# Config & utils
from app.config.qos_profiles import resolve_qos_profile, QoSProfile
from app.config.subscriber_map import resolve_imsi
from app.config import testbed_defaults as tb
from app.config.settings import settings
from app.utils.converters import parse_bitrate_to_kbps
from app.utils.idempotency import build_payload_fingerprint
from app.store.repositories import OperationRepository, IdempotencyRepository, SliceRegistryRepository

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
        self.operation_repo = OperationRepository()
        self.idempotency_repo = IdempotencyRepository()
        self.slice_registry_repo = SliceRegistryRepository()
        # Per-(snssai, dnn) asyncio locks to prevent concurrent slice creation races
        self._slice_locks: Dict[tuple[str, str], asyncio.Lock] = {}

    def _get_slice_lock(self, snssai: str, dnn: str) -> asyncio.Lock:
        key = (snssai, dnn)
        if key not in self._slice_locks:
            self._slice_locks[key] = asyncio.Lock()
        return self._slice_locks[key]

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
    #  Internal: deterministic slice ID derivation                        #
    # ================================================================== #

    @staticmethod
    def _derive_slice_id(sst: int, sd: Optional[str], dnn: str) -> str:
        """
        Derive a stable, human-readable SM slice ID from SNSSAI + DNN.

        Format: ``s{sst}d{sd}-{dnn}``  e.g. ``s1d000001-internet``

        The SM uses this as ``slice_name`` / ``tplname`` in hardware, so it
        must be consistent across calls for the same logical slice.
        Allowed chars: alphanumeric, hyphen, underscore.
        """
        safe_sd = sd or "000000"
        safe_dnn = dnn.replace(".", "-").replace("_", "-")
        return f"s{sst}d{safe_sd}-{safe_dnn}"

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
            "dnn": dnn,
        }
        if sd:
            payload["sd"] = sd
        _add_if_not_none(payload, "coverage_area", settings.sm_default_coverage_area)

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
    # ================================================================== #
    #  Internal: build SM dissociate_slice payload                        #
    # ================================================================== #
    @staticmethod
    def _build_dissociate_payload(
        imsi: str,
        slice_id: str,
        snssai: str,
    ) -> Dict[str, Any]:
        """Build the ``CoreSliceDissociatePostRequest`` dict."""
        return {
            "imsi": imsi,
            "slice": slice_id,
            "snssai": snssai,
        }

    def _build_associate_payload(
        self,
        body: AsSessionWithQoSSubscription,
        slice_id: str,
        imsi: str,
        dnn: str,
        snssai: str,
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
            "dnn": dnn,
            "snssai": snssai,
        }

    # ================================================================== #
    #  Internal: build SM change_slice payload                            #
    # ================================================================== #
    def _build_change_payload(
        self,
        imsi: str,
        slice_id: str,
        snssai: str,
        dnn: str,
    ) -> Dict[str, Any]:
        """Build the ``CoreSliceChangePostRequest`` dict (all 4 fields required)."""
        return {
            "imsi": imsi,
            "slice": slice_id,
            "dnn": dnn,
            "snssai": snssai,
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
        idempotency_key: Optional[str] = None,
    ) -> Union[AsSessionWithQoSSubscription, OperationAccepted]:
        """
        Full create flow with idempotency guard:
          1. Reserve idempotency key/fingerprint
          2. Resolve QoS profile and IMSI
          3. Determine SNSSAI and IDs
          4. Send SM create + associate
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

        self.operation_repo.create(
            operation_id=operation_id,
            scs_as_id=scs_as_id,
            idempotency_key=idempotency_key,
            payload_fingerprint=payload_fingerprint,
            status="pending",
        )

        # -- 1. QoS profile -------------------------------------------------
        qos: Optional[QoSProfile] = None
        if body.qosReference:
            try:
                qos = resolve_qos_profile(body.qosReference)
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

        # -- 3. SNSSAI -------------------------------------------------------
        sst, sd, _ = self._resolve_snssai(body, qos)
        sm_snssai = f"{sst}-{sd}" if sd else str(sst)
        dnn = body.dnn or tb.DEFAULT_DNN

        # -- 4. Slice ID (deterministic) + Registry --------------------------
        slice_id = self._derive_slice_id(sst, sd, dnn)
        subscription_id = str(uuid.uuid4())

        async with self._get_slice_lock(sm_snssai, dnn):
            registry_entry, is_new_slice = self.slice_registry_repo.get_or_create(
                snssai=sm_snssai,
                dnn=dnn,
                sm_slice_id=slice_id,
            )
            if not is_new_slice:
                # Reuse the already-provisioned slice
                slice_id = registry_entry["sm_slice_id"]

            logger.info(
                "Creating subscription  scs=%s  sub=%s  slice=%s  imsi=%s  new_slice=%s",
                scs_as_id,
                subscription_id,
                slice_id,
                imsi,
                is_new_slice,
            )

            self.operation_repo.update_status(
                operation_id=operation_id,
                status="published",
                sm_slice_id=slice_id,
            )

            # -- 5. SM create_slice (only when slice is brand-new) -----------
            if is_new_slice:
                create_payload = self._build_create_payload(body, slice_id, qos, sst, sd, dnn)
                logger.info("→ SM create_slice  %s", create_payload)
                try:
                    await self.sm_client.create_slice(create_payload)
                except CircuitBreakerOpen as exc:
                    self.slice_registry_repo.delete(sm_snssai, dnn)
                    self.operation_repo.update_status(
                        operation_id=operation_id, status="failed",
                        sm_slice_id=slice_id, error=str(exc),
                    )
                    raise HTTPException(status_code=503, detail=str(exc))
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 409:
                        # Slice already exists in SM (e.g. translator DB was reset).
                        # Treat it as a pre-existing slice — skip rollback and proceed
                        # with associate.
                        logger.info(
                            "Slice %s already exists in SM (409) — reusing", slice_id
                        )
                        is_new_slice = False
                    else:
                        self.slice_registry_repo.delete(sm_snssai, dnn)
                        self.operation_repo.update_status(
                            operation_id=operation_id, status="failed",
                            sm_slice_id=slice_id, error=str(exc),
                        )
                        raise HTTPException(
                            status_code=502,
                            detail=f"SM create_slice failed: {exc}",
                        )
                except Exception as exc:
                    self.slice_registry_repo.delete(sm_snssai, dnn)
                    self.operation_repo.update_status(
                        operation_id=operation_id, status="failed",
                        sm_slice_id=slice_id, error=str(exc),
                    )
                    raise HTTPException(
                        status_code=502, detail=f"SM create_slice failed: {exc}"
                    )

            # -- 6. SM associate_slice (with rollback) -----------------------
            associate_payload = self._build_associate_payload(
                body,
                slice_id,
                imsi,
                dnn,
                sm_snssai,
            )
            logger.info("→ SM associate_slice  %s", associate_payload)
            try:
                await self.sm_client.associate_slice(associate_payload)
            except CircuitBreakerOpen as exc:
                if is_new_slice:
                    try:
                        await self.sm_client.delete_slice({"id": slice_id})
                    except Exception:
                        pass
                    self.slice_registry_repo.delete(sm_snssai, dnn)
                self.operation_repo.update_status(
                    operation_id=operation_id,
                    status="failed",
                    sm_slice_id=slice_id,
                    error=str(exc),
                )
                raise HTTPException(status_code=503, detail=str(exc))
            except Exception as exc:
                logger.error("SM associate_slice failed: %s", exc)
                if is_new_slice:
                    try:
                        await self.sm_client.delete_slice({"id": slice_id})
                        logger.info("Rollback: deleted orphaned slice %s", slice_id)
                    except Exception as rb_exc:
                        logger.error("Rollback delete_slice also failed: %s", rb_exc)
                    self.slice_registry_repo.delete(sm_snssai, dnn)
                self.operation_repo.update_status(
                    operation_id=operation_id,
                    status="failed",
                    sm_slice_id=slice_id,
                    error=str(exc),
                )
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"SM associate_slice failed"
                        f"{' (create rolled back)' if is_new_slice else ''}: {exc}"
                    ),
                )

            # Associate succeeded — increment ref count
            self.slice_registry_repo.increment_ref(sm_snssai, dnn)

        # -- 7. Store subscription -------------------------------------------
        sub_data = self._sub_to_dict(body, scs_as_id, subscription_id)
        store.create(
            scs_as_id=scs_as_id,
            subscription_id=subscription_id,
            sm_slice_id=slice_id,
            imsi=imsi,
            data=sub_data,
            operation_id=operation_id,
        )

        self.operation_repo.update_status(
            operation_id=operation_id,
            status="completed",
            subscription_id=subscription_id,
            sm_slice_id=slice_id,
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
        sst, sd, _ = self._resolve_snssai(body, qos)
        sm_snssai = f"{sst}-{sd}" if sd else str(sst)
        dnn = body.dnn or tb.DEFAULT_DNN

        # SM change_slice
        change_payload = self._build_change_payload(record.imsi, record.sm_slice_id, sm_snssai, dnn)
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

            sst, sd, _ = self._resolve_snssai(merged_sub, qos)
            sm_snssai = f"{sst}-{sd}" if sd else str(sst)
            dnn = merged_sub.dnn or tb.DEFAULT_DNN

            change_payload = self._build_change_payload(record.imsi, record.sm_slice_id, sm_snssai, dnn)
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
        """Dissociate the UE, decrement the slice ref count, and delete the
        slice from SM only when the last subscriber is removed."""
        record = store.get(scs_as_id, subscription_id)
        if not record:
            raise HTTPException(status_code=404, detail="Subscription not found")

        slice_id = record.sm_slice_id
        imsi = record.imsi

        registry_entry = self.slice_registry_repo.get_by_slice_id(slice_id)

        if registry_entry is not None:
            sm_snssai = registry_entry["snssai"]
            dnn = registry_entry["dnn"]

            async with self._get_slice_lock(sm_snssai, dnn):
                # Dissociate UE from the slice
                dissociate_payload = self._build_dissociate_payload(imsi, slice_id, sm_snssai)
                logger.info(
                    "→ SM dissociate_slice  sub=%s  slice=%s  imsi=%s",
                    subscription_id, slice_id, imsi,
                )
                try:
                    await self._call_sm(
                        self.sm_client.dissociate_slice(dissociate_payload),
                        "SM dissociate_slice",
                    )
                except HTTPException as exc:
                    logger.warning(
                        "SM dissociate_slice failed (continuing): %s", exc.detail
                    )

                new_ref_count = self.slice_registry_repo.decrement_ref(sm_snssai, dnn)
                logger.info("Slice %s ref_count now %d", slice_id, new_ref_count)

                if new_ref_count == 0:
                    logger.info("→ SM delete_slice  slice=%s", slice_id)
                    await self._call_sm(
                        self.sm_client.delete_slice({"id": slice_id}), "SM delete_slice"
                    )
                    self.slice_registry_repo.delete(sm_snssai, dnn)
        else:
            # Legacy subscription without a registry entry — delete directly
            logger.warning(
                "No registry entry for slice %s — falling back to direct delete", slice_id
            )
            await self._call_sm(
                self.sm_client.delete_slice({"id": slice_id}), "SM delete_slice"
            )

        store.delete(scs_as_id, subscription_id)
