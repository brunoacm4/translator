# coding: utf-8

"""
    Policy Authorization Service — TS 29.514 Npcf_PolicyAuthorization (N5)

    The NEF calls a PCF over N5; the translator answers as that PCF
    (docs/adr/0001) and maps each Individual Application Session Context onto
    the translation core (``app.impl.session_core``). Minimal scope
    (docs/adr/0003):

      POST  /app-sessions                        →  core start  (SM associate)
      PATCH /app-sessions/{appSessionId}         →  core change (medComponents changed)
      POST  /app-sessions/{appSessionId}/delete  →  core stop   (SM dissociate)

    plus, like the Open5GS PCF (docs/adr/0007), one notification only: the
    termination request ``TerminationInfo`` to ``{ascReqData.notifUri}/terminate``
    when the SM reports that a create failed. The session is then forgotten.
    No ``EventsNotification`` is sent.

    Status codes follow the Open5GS 2.8.0 PCF the NEF is built against:
    unknown UE → 404; missing, unknown or conflicting ``qosReference``, or a
    ``sliceInfo`` / ``dnn`` that does not match the profile's slice → 403. A
    second session for a UE that already has one → 403, since the create
    operation lists no 409 (docs/adr/0006).

    Sessions share the subscriptions table with TS 29.122, under the reserved
    owner ``N5_OWNER``. The stored data is ``{"context": <AppSessionContext as
    received>, "resourceUri": <absolute URI of the session>}``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException
from pydantic import ValidationError

from app.config.subscriber_map import resolve_imsi
from app.impl.session_core import (
    QosIntent,
    SessionCore,
    UeSessionExists,
    bitrate_kbps,
    resolve_qos,
)
from app.models.pcf.app_session import (
    AppSessionContext,
    AppSessionContextReqData,
    AppSessionContextUpdateDataPatch,
    TerminationInfo,
)
from app.store.subscription_store import store
from app.utils.merge_patch import apply_merge_patch

logger = logging.getLogger(__name__)

# Owner of N5 sessions in the subscriptions table. '#' cannot reach a
# TS 29.122 scsAsId unescaped, so it cannot collide with an AF's subscriptions.
N5_OWNER = "#npcf-policyauthorization"

# Cause sent when the SM could not set up a new session (docs/adr/0007).
# Chosen from the TS 29.514 V17.6.0 OpenAPI enum by name; the spec text that
# defines each cause has not been read.
TERMINATION_CAUSE_FAILED_CREATE = "INSUFFICIENT_QOS_FLOW_RESOURCES"


def build_termination_request(
    stored: Dict[str, Any],
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """
    Build the termination request of a stored session (docs/adr/0007).

    Sent to ``{ascReqData.notifUri}/terminate``, the route the Open5GS PCF
    uses and the NEF's southbound gateway serves. The Open5GS PCF sends no
    body; this sends the spec's ``TerminationInfo``, which the NEF's route
    (a JSON object is required) also accepts.

    Returns ``(url, payload)``, or ``(None, None)`` without ``notifUri``.
    """
    req = (stored.get("context") or {}).get("ascReqData") or {}
    if not req.get("notifUri"):
        return None, None
    info = TerminationInfo(
        termCause=TERMINATION_CAUSE_FAILED_CREATE,
        resUri=stored["resourceUri"],
    )
    url = f"{req['notifUri'].rstrip('/')}/terminate"
    return url, info.model_dump(mode="json", exclude_none=True)


class PolicyAuthorizationService:
    """Translates N5 application sessions into translation-core sessions."""

    def __init__(self) -> None:
        self.core = SessionCore()
        self.operation_repo = self.core.operation_repo

    # ================================================================== #
    #  Internal: N5 request → interface-neutral QoS intent                #
    # ================================================================== #
    @staticmethod
    def _intent(req: AppSessionContextReqData) -> QosIntent:
        """
        Map ``ascReqData`` onto a ``QosIntent``.

        The SM associates a UE with one slice, so one session carries one QoS
        profile: media components naming different ``qosReference`` values
        are rejected. ``sliceInfo`` and ``dnn`` must match the profile's slice.
        Bitrates come from the lowest-numbered component (``mirBw*`` → GBR,
        ``marBw*`` → MBR); they are parsed but not applied until decision D3
        (docs/adr/0006).

        Raises:
            ValueError: conflicting ``qosReference`` values.
        """
        components = sorted((req.medComponents or {}).values(), key=lambda m: m.medCompN)
        references = sorted({m.qosReference for m in components if m.qosReference})
        if len(references) > 1:
            raise ValueError(
                f"Media components request different qosReference values {references}; "
                "one QoS profile applies per UE session"
            )
        first = components[0] if components else None
        return QosIntent(
            qos_reference=references[0] if references else None,
            sst=req.sliceInfo.sst if req.sliceInfo else None,
            sd=req.sliceInfo.sd if req.sliceInfo else None,
            dnn=req.dnn,
            gbr_dl_kbps=bitrate_kbps(first.mirBwDl) if first else None,
            gbr_ul_kbps=bitrate_kbps(first.mirBwUl) if first else None,
            mbr_dl_kbps=bitrate_kbps(first.marBwDl) if first else None,
            mbr_ul_kbps=bitrate_kbps(first.marBwUl) if first else None,
        )

    def _get(self, app_session_id: str):
        record = store.get(N5_OWNER, app_session_id)
        if not record:
            raise HTTPException(status_code=404, detail="Application session not found")
        return record

    # ------------------------------------------------------------------ #
    #  POST /app-sessions — Create                                        #
    # ------------------------------------------------------------------ #
    async def create(
        self,
        context: AppSessionContext,
        collection_uri: str,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Start the session and store the context.

        Args:
            context:         the AppSessionContext as received.
            collection_uri:  absolute URI of ``/app-sessions``, for the Location.

        Returns:
            (absolute URI of the new session, the stored context)
        """
        req = context.ascReqData
        if req is None:
            raise HTTPException(status_code=400, detail="ascReqData is required")

        data = context.model_dump(mode="json", exclude_none=True)
        operation_id = str(uuid.uuid4())
        self.operation_repo.create(operation_id=operation_id, scs_as_id=N5_OWNER, status="pending")

        try:
            resolved = resolve_qos(self._intent(req))
        except ValueError as exc:
            self.operation_repo.update_status(
                operation_id=operation_id, status="failed", error=str(exc)
            )
            raise HTTPException(status_code=403, detail=str(exc))

        try:
            imsi = resolve_imsi(ue_ipv4=req.ueIpv4, ue_ipv6=req.ueIpv6)
        except ValueError as exc:
            self.operation_repo.update_status(
                operation_id=operation_id, status="failed", error=str(exc)
            )
            raise HTTPException(status_code=404, detail=str(exc))

        app_session_id = str(uuid.uuid4())
        resource_uri = f"{collection_uri.rstrip('/')}/{app_session_id}"
        async with self.core.ue_lock(imsi):
            try:
                self.core.ensure_no_session(imsi)
            except UeSessionExists as exc:
                self.operation_repo.update_status(
                    operation_id=operation_id, status="failed", error=str(exc)
                )
                raise HTTPException(status_code=403, detail=str(exc))

            sm_request_id = await self.core.start(
                owner=N5_OWNER,
                session_id=app_session_id,
                resolved=resolved,
                imsi=imsi,
                operation_id=operation_id,
            )
            store.create(
                scs_as_id=N5_OWNER,
                subscription_id=app_session_id,
                sm_slice_id=resolved.slice_id,
                imsi=imsi,
                data={"context": data, "resourceUri": resource_uri},
                operation_id=operation_id,
            )
        self.core.track(
            operation_id=operation_id,
            session_id=app_session_id,
            slice_id=resolved.slice_id,
            sm_request_id=sm_request_id,
        )
        return resource_uri, data

    # ------------------------------------------------------------------ #
    #  PATCH /app-sessions/{appSessionId} — Modify                        #
    # ------------------------------------------------------------------ #
    async def modify(
        self,
        app_session_id: str,
        patch: AppSessionContextUpdateDataPatch,
    ) -> Dict[str, Any]:
        """
        JSON merge patch (RFC 7396) onto the stored context; move the UE when
        ``medComponents`` changed its slice (tracked and notified,
        docs/adr/0006).
        """
        record = self._get(app_session_id)
        stored = record.subscription_data

        # Only the members the client sent; an explicit null removes a member.
        patch_data = patch.model_dump(mode="json", exclude_unset=True)
        merged = apply_merge_patch(stored["context"], patch_data)
        try:
            req = AppSessionContext.model_validate(merged).ascReqData
        except ValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"The patch leaves the session invalid: {exc.errors()[0]['msg']}",
            )
        if req is None:
            raise HTTPException(status_code=400, detail="The patch removes ascReqData")

        slice_id: Optional[str] = None
        if "medComponents" in (patch_data.get("ascReqData") or {}):
            try:
                resolved = resolve_qos(self._intent(req))
            except ValueError as exc:
                raise HTTPException(status_code=403, detail=str(exc))
            slice_id = await self.core.change(
                owner=N5_OWNER,
                session_id=app_session_id,
                imsi=record.imsi,
                current_slice_id=record.sm_slice_id,
                resolved=resolved,
                description=f"(N5 PATCH)  appSession={app_session_id}",
            )

        store.update(
            N5_OWNER, app_session_id, {**stored, "context": merged}, sm_slice_id=slice_id
        )
        return merged

    # ------------------------------------------------------------------ #
    #  POST /app-sessions/{appSessionId}/delete — Delete                  #
    # ------------------------------------------------------------------ #
    async def delete(self, app_session_id: str) -> None:
        """Stop the session in the core and forget it."""
        record = self._get(app_session_id)
        await self.core.stop(
            imsi=record.imsi,
            slice_id=record.sm_slice_id,
            session_id=app_session_id,
        )
        store.delete(N5_OWNER, app_session_id)
