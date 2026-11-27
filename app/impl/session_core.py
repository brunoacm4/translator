# coding: utf-8

"""
    Translation Core — interface-independent QoS sessions

    Both northbound interfaces turn their requests into a ``QosIntent`` and
    hand it to ``SessionCore`` (docs/adr/0001):

      TS 29.122 AsSessionWithQoS          →  app.impl.translator_service
      TS 29.514 Npcf_PolicyAuthorization  →  app.impl.policy_authorization_service

    The Slice Manager pre-creates one slice per QoS profile (model B,
    docs/adr/0004); the ``qosReference`` selects it (docs/adr/0005). The core
    owns every Slice Manager interaction:

      start   →  SM associate_slice (UE onto the profile's slice)
      track   →  record the outcome, poll SM /operations when enabled
      change  →  SM change_slice (UE onto another profile's slice), tracked
      stop    →  SM dissociate_slice

    Request handling under model B follows docs/adr/0006.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Dict, List, Optional

from fastapi import HTTPException

from app.config import testbed_defaults as tb
from app.config.qos_profiles import QoSProfile, resolve_qos_profile
from app.config.settings import settings
from app.impl.sm_client import SliceManagerClient
from app.impl.sm_poller import poll_sm_request
from app.resilience.circuit_breaker import CircuitBreakerOpen
from app.store.repositories import OperationRepository
from app.store.subscription_store import store
from app.utils.converters import parse_bitrate_to_kbps

logger = logging.getLogger(__name__)


def bitrate_kbps(value: Optional[str]) -> Optional[int]:
    """3GPP ``BitRate`` string (e.g. ``"10 Mbps"``) to KBPS; None when absent or unparsable."""
    return parse_bitrate_to_kbps(value) if value else None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  What a UE should get, whichever API asked for it
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass(frozen=True)
class QosIntent:
    """QoS requested for one UE, independent of the northbound interface.

    Only ``qos_reference`` selects the slice. ``sst``/``sd``/``dnn`` are
    checked against that slice. The latency, priority and bitrate members
    are parsed but not applied: how to apply them is decision D3 of the
    architecture review (docs/adr/0006).
    """

    qos_reference: Optional[str] = None
    sst: Optional[int] = None             # explicit S-NSSAI from the request, if any
    sd: Optional[str] = None
    dnn: Optional[str] = None
    latency_ms: Optional[int] = None
    priority: Optional[int] = None
    gbr_dl_kbps: Optional[int] = None
    gbr_ul_kbps: Optional[int] = None
    mbr_dl_kbps: Optional[int] = None
    mbr_ul_kbps: Optional[int] = None


@dataclass(frozen=True)
class ResolvedQos:
    """A ``QosIntent`` with the profile, and so the SM slice, it selects."""

    intent: QosIntent
    profile: QoSProfile

    @property
    def slice_id(self) -> str:
        return self.profile.slice_id

    @property
    def dnn(self) -> str:
        return self.profile.dnn

    @property
    def sm_snssai(self) -> str:
        """S-NSSAI of the slice in the SM's notation, e.g. ``1-000001``."""
        return self.profile.sm_snssai


def resolve_qos(intent: QosIntent) -> ResolvedQos:
    """
    Select the QoS profile of an intent (docs/adr/0006, decisions 1 and 2).

    Raises:
        ValueError: no ``qos_reference``, an unknown one, or an S-NSSAI / DNN
            in the request that differs from the selected profile's slice.
    """
    if not intent.qos_reference:
        raise ValueError(
            "A qosReference is required: it selects the slice of the session"
        )
    profile = resolve_qos_profile(intent.qos_reference)

    mismatches: List[str] = []
    if intent.sst is not None:
        sd_differs = intent.sd is not None and intent.sd.lower() != (profile.sd or "").lower()
        if intent.sst != profile.sst or sd_differs:
            requested = f"{intent.sst}-{intent.sd}" if intent.sd else str(intent.sst)
            mismatches.append(f"S-NSSAI {requested} (slice has {profile.sm_snssai})")
    if intent.dnn is not None and intent.dnn != profile.dnn:
        mismatches.append(f"DNN '{intent.dnn}' (slice has '{profile.dnn}')")
    if mismatches:
        raise ValueError(
            f"qosReference '{intent.qos_reference}' selects slice '{profile.slice_id}', "
            f"which does not match the requested {' and '.join(mismatches)}"
        )
    return ResolvedQos(intent=intent, profile=profile)


class UeSessionExists(Exception):
    """The UE already has a QoS session (docs/adr/0006, decision 3)."""

    def __init__(self, imsi: str) -> None:
        super().__init__(
            "The UE already has an active QoS session; delete it before creating another"
        )
        self.imsi = imsi


# Per-UE locks, shared by every SessionCore instance of the process, so that
# the "one session per UE" check and the create cannot interleave.
_ue_locks: Dict[str, asyncio.Lock] = {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Slice Manager orchestration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SessionCore:
    """Slice Manager side of every QoS session, shared by both APIs."""

    def __init__(self) -> None:
        self.sm_client = SliceManagerClient()
        self.operation_repo = OperationRepository()

    # ================================================================== #
    #  One session per UE                                                 #
    # ================================================================== #

    @staticmethod
    def ue_lock(imsi: str) -> asyncio.Lock:
        """The process-wide lock that serialises session creation for a UE."""
        if imsi not in _ue_locks:
            _ue_locks[imsi] = asyncio.Lock()
        return _ue_locks[imsi]

    @staticmethod
    def ensure_no_session(imsi: str) -> None:
        """Raise ``UeSessionExists`` if the UE has a session in either API.

        Call it while holding ``ue_lock(imsi)``, up to the session's store.
        """
        if store.get_by_imsi(imsi) is not None:
            raise UeSessionExists(imsi)

    # ================================================================== #
    #  SM call wrapper                                                    #
    # ================================================================== #

    @staticmethod
    async def call_sm(coro: Awaitable[str], description: str) -> str:
        """
        Await an SM coroutine, translating exceptions to HTTP status codes.
        Returns the SM ``request_id`` string on success.
        """
        try:
            return await coro
        except CircuitBreakerOpen as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"{description} failed: {exc}")

    @staticmethod
    def poll_untracked(sm_request_id: str) -> None:
        """Follow an SM write that has no local operation record (log only).

        An empty ID is skipped (e.g. a tolerated 404 on dissociate).
        """
        if settings.sm_polling_enabled and sm_request_id:
            asyncio.create_task(
                poll_sm_request(sm_request_id=sm_request_id, operation_id=""),
                name=f"poll-{sm_request_id[:8]}",
            )

    # ================================================================== #
    #  SM payloads                                                        #
    # ================================================================== #

    @staticmethod
    def build_associate_payload(slice_id: str, snssai: str, dnn: str) -> Dict[str, Any]:
        """
        Build the ``UeSliceAssociationCreateRequest`` dict.
        IMSI is passed separately as ``ue_id`` path parameter.

        ``static_ipv4_address`` is deliberately not sent: the UE address in
        the northbound request identifies the UE by its current address,
        whereas the SM field pins a static IP in UDM (it must fall inside the
        DNN's UDG pool). The translator only uses the UE IP to resolve the IMSI.
        """
        return {
            "slice_id": slice_id,
            "snssai": snssai,
            "dnn": dnn,
            "access_mobility_data": True,
            "snssai_advertisement_allowed": True,
            "uplink_aggregate_maximum_bitrate_kbps": tb.DEFAULT_AMBR_UP,
            "downlink_aggregate_maximum_bitrate_kbps": tb.DEFAULT_AMBR_DW,
        }

    @staticmethod
    def build_change_payload(slice_id: str, snssai: str, dnn: str) -> Dict[str, Any]:
        """Build the ``UeSliceAssociationUpdateRequest`` dict.
        IMSI is passed separately as ``ue_id`` path parameter.
        """
        return {
            "slice_id": slice_id,
            "snssai": snssai,
            "dnn": dnn,
        }

    # ================================================================== #
    #  start — associate the UE with the profile's slice                  #
    # ================================================================== #

    async def start(
        self,
        *,
        owner: str,
        session_id: str,
        resolved: ResolvedQos,
        imsi: str,
        operation_id: str,
    ) -> str:
        """
        Associate the UE with the pre-provisioned slice of its profile.

        The operation record is marked failed if the SM rejects the call.

        Returns:
            The SM request ID of the associate ("" if the SM returned none).
        """
        slice_id = resolved.slice_id
        logger.info(
            "Starting session  owner=%s  session=%s  slice=%s  imsi=%s",
            owner, session_id, slice_id, imsi,
        )
        self.operation_repo.update_status(
            operation_id=operation_id, status="published", sm_slice_id=slice_id
        )

        payload = self.build_associate_payload(slice_id, resolved.sm_snssai, resolved.dnn)
        logger.info("→ SM associate_slice  imsi=%s  slice=%s", imsi, slice_id)
        try:
            sm_req_id = await self.call_sm(
                self.sm_client.associate_slice(imsi, payload), "SM associate_slice"
            )
        except HTTPException as exc:
            self.operation_repo.update_status(
                operation_id=operation_id, status="failed",
                sm_slice_id=slice_id, error=str(exc.detail),
            )
            raise
        return sm_req_id

    # ================================================================== #
    #  track — record the outcome of an SM write                          #
    # ================================================================== #

    def track(
        self,
        *,
        operation_id: str,
        session_id: str,
        slice_id: str,
        sm_request_id: str,
        previous_slice_id: Optional[str] = None,
    ) -> None:
        """Mark the operation provisioning (and poll the SM) or completed.

        ``previous_slice_id`` marks a QoS change: if the SM reports it failed,
        the poller puts the session back on that slice.
        """
        sm_request_id_col = sm_request_id or None

        if settings.sm_polling_enabled and sm_request_id:
            # Async tracking: mark provisioning and poll SM /operations until
            # every SM request is terminal, then notify the application.
            self.operation_repo.update_status(
                operation_id=operation_id,
                status="sm_provisioning",
                subscription_id=session_id,
                sm_slice_id=slice_id,
                sm_request_id=sm_request_id_col,
                previous_sm_slice_id=previous_slice_id,
            )
            asyncio.create_task(
                poll_sm_request(sm_request_id=sm_request_id, operation_id=operation_id),
                name=f"poll-{operation_id[:8]}",
            )
        else:
            # Polling disabled (older SM without /operations): the synchronous
            # 202 (state="published") is the terminal result we can observe, so
            # the operation is complete once the SM has accepted the request.
            self.operation_repo.update_status(
                operation_id=operation_id,
                status="completed",
                subscription_id=session_id,
                sm_slice_id=slice_id,
                sm_request_id=sm_request_id_col,
                previous_sm_slice_id=previous_slice_id,
            )

    # ================================================================== #
    #  change — move the UE to the slice of another profile               #
    # ================================================================== #

    async def change(
        self,
        *,
        owner: str,
        session_id: str,
        imsi: str,
        current_slice_id: str,
        resolved: ResolvedQos,
        description: str,
    ) -> str:
        """
        Move the UE to the slice of the new profile (docs/adr/0006, decision 4).

        No SM call when the profile maps to the current slice. Otherwise the
        move is recorded as an operation and tracked like a create; the caller
        stores the returned slice with the session. If the SM later reports
        the move failed, the poller restores ``current_slice_id``.

        Returns:
            The slice the session is on once the SM accepted the move.
        """
        target = resolved.slice_id
        if target == current_slice_id:
            logger.info(
                "QoS change %s  imsi=%s  stays on slice %s: no SM call",
                description, imsi, target,
            )
            return target

        operation_id = str(uuid.uuid4())
        self.operation_repo.create(operation_id=operation_id, scs_as_id=owner, status="pending")

        payload = self.build_change_payload(target, resolved.sm_snssai, resolved.dnn)
        logger.info(
            "→ SM change_slice  %s  imsi=%s  slice %s → %s",
            description, imsi, current_slice_id, target,
        )
        try:
            sm_req_id = await self.call_sm(
                self.sm_client.change_slice(imsi, payload), "SM change_slice"
            )
        except HTTPException as exc:
            self.operation_repo.update_status(
                operation_id=operation_id, status="failed", subscription_id=session_id,
                sm_slice_id=target, previous_sm_slice_id=current_slice_id,
                error=str(exc.detail),
            )
            raise

        self.track(
            operation_id=operation_id,
            session_id=session_id,
            slice_id=target,
            sm_request_id=sm_req_id,
            previous_slice_id=current_slice_id,
        )
        return target

    # ================================================================== #
    #  stop — dissociate the UE                                           #
    # ================================================================== #

    async def stop(self, *, imsi: str, slice_id: str, session_id: str) -> None:
        """Dissociate the UE from its slice. The slice stays: the SM owns it.

        A failed dissociate is logged and the session is still forgotten.
        """
        logger.info(
            "→ SM dissociate_slice  session=%s  slice=%s  imsi=%s",
            session_id, slice_id, imsi,
        )
        try:
            sm_req_id = await self.call_sm(
                self.sm_client.dissociate_slice(imsi, slice_id), "SM dissociate_slice"
            )
        except HTTPException as exc:
            logger.warning("SM dissociate_slice failed (continuing): %s", exc.detail)
            return
        self.poll_untracked(sm_req_id)
