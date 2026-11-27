# coding: utf-8

"""
3GPP TS 29.514 Npcf_PolicyAuthorization data types (V17.6.0, API 1.2.1)

Only the members the translator reads are typed. Every other member of the
spec is accepted and kept as received (``extra="allow"``), so an Individual
Application Session Context is stored and echoed back unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from app.models.nef.common import Snssai

_KEEP_UNKNOWN = {"extra": "allow", "populate_by_name": True}


class MediaComponent(BaseModel):
    """One media component (a value of the ``medComponents`` map)."""

    medCompN: int = Field(..., description="Media component number; also the map key")
    qosReference: Optional[str] = Field(default=None, description="Pre-defined QoS information")
    medType: Optional[str] = Field(default=None, description="AUDIO, VIDEO, DATA, ...")
    marBwDl: Optional[str] = Field(default=None, description="Maximum requested bandwidth DL (BitRate)")
    marBwUl: Optional[str] = Field(default=None, description="Maximum requested bandwidth UL (BitRate)")
    mirBwDl: Optional[str] = Field(default=None, description="Minimum requested bandwidth DL (BitRate)")
    mirBwUl: Optional[str] = Field(default=None, description="Minimum requested bandwidth UL (BitRate)")

    model_config = _KEEP_UNKNOWN


class AfEventSubscription(BaseModel):
    """One event the AF subscribes to."""

    event: str = Field(..., description="e.g. SUCCESSFUL_RESOURCES_ALLOCATION")
    notifMethod: Optional[str] = Field(default=None, description="EVENT_DETECTION, ONE_TIME, ...")

    model_config = _KEEP_UNKNOWN


class EventsSubscReqData(BaseModel):
    """Events the AF subscribes to, and where to notify them."""

    events: List[AfEventSubscription] = Field(..., min_length=1)
    notifUri: Optional[str] = Field(
        default=None, description="Notifications go to {notifUri}/notify"
    )

    model_config = _KEEP_UNKNOWN


class AppSessionContextReqData(BaseModel):
    """Service requirements of an Individual Application Session Context."""

    notifUri: str = Field(..., description="Termination requests go to {notifUri}/terminate")
    suppFeat: str = Field(..., description="Supported features (hex string)")
    ueIpv4: Optional[str] = None
    ueIpv6: Optional[str] = None
    ueMac: Optional[str] = None
    dnn: Optional[str] = None
    sliceInfo: Optional[Snssai] = None
    supi: Optional[str] = None
    afAppId: Optional[str] = None
    medComponents: Optional[Dict[str, MediaComponent]] = Field(
        default=None,
        min_length=1,
        description="Media components keyed by medCompN",
    )
    evSubsc: Optional[EventsSubscReqData] = None

    model_config = _KEEP_UNKNOWN

    @model_validator(mode="after")
    def _ue_address_present(self) -> "AppSessionContextReqData":
        # The spec's oneOf asks for exactly one of these; accepting a
        # dual-stack request (IPv4 and IPv6) is deliberate, as Open5GS does.
        if not (self.ueIpv4 or self.ueIpv6 or self.ueMac):
            raise ValueError("one of ueIpv4, ueIpv6 or ueMac is required")
        return self


class AfEventNotification(BaseModel):
    """One notified event."""

    event: str

    model_config = _KEEP_UNKNOWN


class EventsNotification(BaseModel):
    """Notification of matched events, POSTed to {evSubsc.notifUri}/notify."""

    evSubsUri: str = Field(..., description="URI of the Events Subscription sub-resource")
    evNotifs: List[AfEventNotification] = Field(..., min_length=1)

    model_config = _KEEP_UNKNOWN


class TerminationInfo(BaseModel):
    """Request to terminate an app session, POSTed to {ascReqData.notifUri}/terminate."""

    termCause: str = Field(..., description="TerminationCause, e.g. INSUFFICIENT_QOS_FLOW_RESOURCES")
    resUri: str = Field(..., description="URI of the Individual Application Session Context")

    model_config = _KEEP_UNKNOWN


class AppSessionContext(BaseModel):
    """An Individual Application Session Context resource."""

    ascReqData: Optional[AppSessionContextReqData] = None
    ascRespData: Optional[Dict[str, Any]] = None
    evsNotif: Optional[EventsNotification] = None

    model_config = _KEEP_UNKNOWN


class AppSessionContextUpdateData(BaseModel):
    """
    Modifications to ``ascReqData`` (``application/merge-patch+json``).

    ``medComponents`` values are ``MediaComponentRm``: a partial component, or
    ``null`` to remove that component. They are applied as a merge patch, so
    they are kept as plain objects here.
    """

    medComponents: Optional[Dict[str, Optional[Dict[str, Any]]]] = None
    evSubsc: Optional[Dict[str, Any]] = None

    model_config = _KEEP_UNKNOWN


class AppSessionContextUpdateDataPatch(BaseModel):
    """PATCH body of an Individual Application Session Context."""

    ascReqData: Optional[AppSessionContextUpdateData] = None

    model_config = _KEEP_UNKNOWN
