# coding: utf-8

"""
    Translation Core — interface-independent QoS sessions

    Both northbound interfaces turn their requests into a ``QosIntent`` and
    hand it to ``SessionCore`` (docs/adr/0001):

      TS 29.122 AsSessionWithQoS          →  app.impl.translator_service
      TS 29.514 Npcf_PolicyAuthorization  →  app.impl.policy_authorization_service

    The Slice Manager pre-creates one slice per QoS profile (model B,
    docs/adr/0004); the ``qosReference`` selects it (docs/adr/0005). The core
    owns the rules of a session and every Slice Manager interaction:

      create  →  profile and UE checks, one session per UE, SM associate_slice,
                 store the session, track the SM operation
      change  →  SM change_slice (UE onto another profile's slice), tracked
      stop    →  SM dissociate_slice

    Request handling under model B follows docs/adr/0006.

    Errors are the core's own, never HTTP: a refusal is a ``SessionRejected``
    (``QosRejected``, ``UnknownUe``, ``UeSessionExists``), raised before
    anything is written, and each API maps it to its status code. A failed SM
    call is ``SmUnavailable`` (circuit open) or ``SmFailed``, answered 503 and
    502 by both APIs (``app.apis.problem_details``).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.config import testbed_defaults as tb
from app.config.qos_profiles import QoSProfile, resolve_qos_profile
from app.config.settings import settings
from app.config.subscriber_map import resolve_imsi
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
#  Errors
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SessionRejected(Exception):
    """A request the core refuses, before writing anything. ``str()`` is the reason."""


class QosRejected(SessionRejected):
    """No, unknown or conflicting ``qosReference``, or an S-NSSAI / DNN that
    does not match the profile's slice (docs/adr/0006, decisions 1 and 2)."""


class UnknownUe(SessionRejected):
    """The UE address is not in the subscriber map."""


class UeSessionExists(SessionRejected):
    """The UE already has a QoS session (docs/adr/0006, decision 3)."""

    def __init__(self) -> None:
        super().__init__(
            "The UE already has an active QoS session; delete it before creating another"
        )


class SmUnavailable(Exception):
    """The SM circuit breaker is open: the SM was not called."""


class SmFailed(Exception):
    """An SM call failed: network error, timeout or an error answer."""


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
        QosRejected: no ``qos_reference``, an unknown one, or an S-NSSAI / DNN
            in the request that differs from the selected profile's slice.
    """
    if not intent.qos_reference:
        raise QosRejected("A qosReference is required: it selects the slice of the session")
    try:
        profile = resolve_qos_profile(intent.qos_reference)
    except ValueError as exc:
        raise QosRejected(str(exc)) from None

    mismatches: List[str] = []
    if intent.sst is not None:
        sd_differs = intent.sd is not None and intent.sd.lower() != (profile.sd or "").lower()
        if intent.sst != profile.sst or sd_differs:
            requested = f"{intent.sst}-{intent.sd}" if intent.sd else str(intent.sst)
            mismatches.append(f"S-NSSAI {requested} (slice has {profile.sm_snssai})")
    if intent.dnn is not None and intent.dnn != profile.dnn:
        mismatches.append(f"DNN '{intent.dnn}' (slice has '{profile.dnn}')")
    if mismatches:
        raise QosRejected(
            f"qosReference '{intent.qos_reference}' selects slice '{profile.slice_id}', "
            f"which does not match the requested {' and '.join(mismatches)}"
        )
    return ResolvedQos(intent=intent, profile=profile)


def resolve_ue(ue_ipv4: Optional[str], ue_ipv6: Optional[str]) -> str:
    """IMSI of the UE with this address.

    Raises:
        UnknownUe: the address is not in the subscriber map.
    """
    try:
        return resolve_imsi(ue_ipv4=ue_ipv4, ue_ipv6=ue_ipv6)
    except ValueError as exc:
        raise UnknownUe(str(exc)) from None


# Per-UE locks, shared by every SessionCore instance of the process, so that
# the "one session per UE" check and the create cannot interleave.
_ue_locks: Dict[str, asyncio.Lock] = {}


def _ue_lock(imsi: str) -> asyncio.Lock:
    if imsi not in _ue_locks:
        _ue_locks[imsi] = asyncio.Lock()
    return _ue_locks[imsi]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Slice Manager orchestration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SessionCore:
    """The rules of every QoS session and its Slice Manager side, shared by both APIs."""

    def __init__(self) -> None:
        self.sm_client = SliceManagerClient()
        self.operation_repo = OperationRepository()

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
    #  create — a new session                                             #
    # ================================================================== #

    async def create(
        self,
        *,
        owner: str,
        intent: QosIntent,
        ue_ipv4: Optional[str],
        ue_ipv6: Optional[str],
        session_data: Callable[[str], Dict[str, Any]],
        operation_id: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Create a session: select the profile's slice, resolve the UE, enforce
        one session per UE, associate the UE in the SM, store the session and
        track the SM operation (docs/adr/0006).

        Args:
            owner:         the session's owner: the 29.122 scsAsId, or N5_OWNER.
            session_data:  builds what is stored for the session, from its ID.
            operation_id:  fixed in advance by the caller (29.122 idempotency);
                           generated when omitted.

        Returns:
            (session ID, stored data)

        Raises:
            QosRejected, UnknownUe, UeSessionExists: refused; nothing written.
            SmUnavailable, SmFailed: the SM call failed; the operation is
                recorded as failed and no session is stored.
        """
        resolved = resolve_qos(intent)
        imsi = resolve_ue(ue_ipv4, ue_ipv6)
        session_id = str(uuid.uuid4())
        operation_id = operation_id or str(uuid.uuid4())
        data = session_data(session_id)

        async with _ue_lock(imsi):
            if store.get_by_imsi(imsi) is not None:
                raise UeSessionExists()

            logger.info(
                "→ SM associate_slice  owner=%s  session=%s  slice=%s  imsi=%s",
                owner, session_id, resolved.slice_id, imsi,
            )
            sm_request_id = await self._tracked_sm_call(
                owner=owner,
                operation_id=operation_id,
                slice_id=resolved.slice_id,
                call=self.sm_client.associate_slice(
                    imsi,
                    self.build_associate_payload(
                        resolved.slice_id, resolved.sm_snssai, resolved.dnn
                    ),
                ),
                description="SM associate_slice",
            )
            store.create(
                scs_as_id=owner,
                subscription_id=session_id,
                sm_slice_id=resolved.slice_id,
                imsi=imsi,
                data=data,
                operation_id=operation_id,
            )

        self._track(
            operation_id=operation_id,
            session_id=session_id,
            slice_id=resolved.slice_id,
            sm_request_id=sm_request_id,
        )
        return session_id, data

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

        Raises:
            SmUnavailable, SmFailed: the SM call failed; the operation is
                recorded as failed.
        """
        target = resolved.slice_id
        if target == current_slice_id:
            logger.info(
                "QoS change %s  imsi=%s  stays on slice %s: no SM call",
                description, imsi, target,
            )
            return target

        operation_id = str(uuid.uuid4())
        logger.info(
            "→ SM change_slice  %s  imsi=%s  slice %s → %s",
            description, imsi, current_slice_id, target,
        )
        sm_request_id = await self._tracked_sm_call(
            owner=owner,
            operation_id=operation_id,
            slice_id=target,
            call=self.sm_client.change_slice(
                imsi, self.build_change_payload(target, resolved.sm_snssai, resolved.dnn)
            ),
            description="SM change_slice",
            session_id=session_id,
            previous_slice_id=current_slice_id,
        )
        self._track(
            operation_id=operation_id,
            session_id=session_id,
            slice_id=target,
            sm_request_id=sm_request_id,
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
            sm_request_id = await self._call_sm(
                self.sm_client.dissociate_slice(imsi, slice_id), "SM dissociate_slice"
            )
        except (SmUnavailable, SmFailed) as exc:
            logger.warning("SM dissociate_slice failed (continuing): %s", exc)
            return
        # No local operation record: the dissociate is polled for logging only.
        if settings.sm_polling_enabled and sm_request_id:
            asyncio.create_task(
                poll_sm_request(sm_request_id=sm_request_id, operation_id=""),
                name=f"poll-{sm_request_id[:8]}",
            )

    # ================================================================== #
    #  SM calls and operation tracking                                    #
    # ================================================================== #

    @staticmethod
    async def _call_sm(call: Awaitable[str], description: str) -> str:
        """Await an SM call and return its ``request_id``.

        Raises:
            SmUnavailable: the circuit breaker is open.
            SmFailed: any other failure.
        """
        try:
            return await call
        except CircuitBreakerOpen as exc:
            raise SmUnavailable(str(exc)) from exc
        except Exception as exc:
            raise SmFailed(f"{description} failed: {exc}") from exc

    async def _tracked_sm_call(
        self,
        *,
        owner: str,
        operation_id: str,
        slice_id: str,
        call: Awaitable[str],
        description: str,
        session_id: Optional[str] = None,
        previous_slice_id: Optional[str] = None,
    ) -> str:
        """Record an operation (pending), make the SM call, and mark the
        operation failed if the call fails."""
        self.operation_repo.create(operation_id=operation_id, scs_as_id=owner, status="pending")
        try:
            return await self._call_sm(call, description)
        except (SmUnavailable, SmFailed) as exc:
            self.operation_repo.update_status(
                operation_id=operation_id, status="failed", subscription_id=session_id,
                sm_slice_id=slice_id, previous_sm_slice_id=previous_slice_id, error=str(exc),
            )
            raise

    def _track(
        self,
        *,
        operation_id: str,
        session_id: str,
        slice_id: str,
        sm_request_id: str,
        previous_slice_id: Optional[str] = None,
    ) -> None:
        """Mark an accepted operation provisioning (and poll the SM) or completed.

        ``previous_slice_id`` marks a QoS change: if the SM reports it failed,
        the poller puts the session back on that slice.
        """
        polling = settings.sm_polling_enabled and bool(sm_request_id)
        self.operation_repo.update_status(
            operation_id=operation_id,
            # Polling disabled (an older SM without /operations): the SM's 202
            # is the terminal result we can observe.
            status="sm_provisioning" if polling else "completed",
            subscription_id=session_id,
            sm_slice_id=slice_id,
            sm_request_id=sm_request_id or None,
            previous_sm_slice_id=previous_slice_id,
        )
        if polling:
            asyncio.create_task(
                poll_sm_request(sm_request_id=sm_request_id, operation_id=operation_id),
                name=f"poll-{operation_id[:8]}",
            )
