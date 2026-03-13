# coding: utf-8

"""
AsSessionWithQoSSubscription — main request/response model

Maps directly to the ``AsSessionWithQoSSubscription`` schema defined in
3GPP TS 29.122 (``3gpp-as-session-with-qos``).

The same model is used for both the **inbound request body** (POST / PUT)
and the **outbound response** (GET / POST 201 / PUT 200).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.nef.common import (
    FlowInfo,
    QosMonitoringInformation,
    Snssai,
    TscQosRequirement,
    UserPlaneEvent,
)


class AsSessionWithQoSSubscription(BaseModel):
    """
    Represents an individual AS session with required QoS subscription.

    ``notificationDestination`` is the only **required** field per the spec.
    All other fields are optional — the translator inspects specific ones
    (``qosReference``, ``snssai``, ``ueIpv4Addr``, ``tscQosReq``, ``dnn``)
    during translation and stores the rest unchanged.
    """

    # ── Self-link (set by translator on responses) ─────────────────────
    self_link: Optional[str] = Field(
        default=None,
        alias="self",
        description="URI of this subscription resource (set by the server)",
    )
    supportedFeatures: Optional[str] = Field(
        default=None,
        description="Negotiated supported features (hex string)",
    )

    # ── Network slice identification ───────────────────────────────────
    dnn: Optional[str] = Field(
        default=None,
        description="Data Network Name",
    )
    snssai: Optional[Snssai] = Field(
        default=None,
        description="Single Network Slice Selection Assistance Information",
    )

    # ── Notification ───────────────────────────────────────────────────
    notificationDestination: str = Field(
        ...,
        description="Callback URL for asynchronous user-plane event notifications",
    )

    # ── Application identification ─────────────────────────────────────
    exterAppId: Optional[str] = Field(
        default=None,
        description="External Application Identifier",
    )

    # ── Flow descriptions ──────────────────────────────────────────────
    flowInfo: Optional[List[FlowInfo]] = Field(
        default=None,
        min_length=1,
        description="IP data flows requiring QoS",
    )
    ethFlowInfo: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        min_length=1,
        description="Ethernet packet flows (stored as-is)",
    )
    enEthFlowInfo: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        min_length=1,
        description="Enhanced Ethernet flows (stored as-is)",
    )

    # ── QoS parameters ─────────────────────────────────────────────────
    qosReference: Optional[str] = Field(
        default=None,
        description="Pre-defined QoS reference identifier",
    )
    altQoSReferences: Optional[List[str]] = Field(
        default=None,
        min_length=1,
        description="Ordered list of alternative pre-defined QoS references",
    )
    altQosReqs: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        min_length=1,
        description="Alternative service requirement sets (stored as-is)",
    )
    disUeNotif: Optional[bool] = Field(
        default=None,
        description="Disable QoS flow parameter signalling to UE",
    )

    # ── UE identification ──────────────────────────────────────────────
    ueIpv4Addr: Optional[str] = Field(
        default=None,
        description="UE IPv4 address",
    )
    ipDomain: Optional[str] = Field(
        default=None,
        description="IPv4 address domain identifier",
    )
    ueIpv6Addr: Optional[str] = Field(
        default=None,
        description="UE IPv6 address (prefix)",
    )
    macAddr: Optional[str] = Field(
        default=None,
        description="UE MAC address",
    )

    # ── Usage / sponsoring / monitoring ────────────────────────────────
    usageThreshold: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Usage threshold (stored as-is)",
    )
    sponsorInfo: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Sponsor information (stored as-is)",
    )
    qosMonInfo: Optional[QosMonitoringInformation] = Field(
        default=None,
        description="QoS monitoring configuration",
    )

    # ── Misc ───────────────────────────────────────────────────────────
    directNotifInd: Optional[bool] = Field(
        default=None,
        description="Request direct event notification",
    )
    tscQosReq: Optional[TscQosRequirement] = Field(
        default=None,
        description="QoS requirements for time-sensitive communication",
    )
    requestTestNotification: Optional[bool] = Field(
        default=None,
        description="Request a test notification",
    )
    websockNotifConfig: Optional[Dict[str, Any]] = Field(
        default=None,
        description="WebSocket notification configuration (stored as-is)",
    )
    events: Optional[List[UserPlaneEvent]] = Field(
        default=None,
        min_length=1,
        description="User-plane events to subscribe to",
    )

    model_config = {
        "populate_by_name": True,
        "validate_assignment": True,
        # Allow both "self" (alias) and "self_link" (field name) in JSON
    }


class AsSessionWithQoSSubscriptionPatch(BaseModel):
    """
    Partial update model for PATCH ``application/merge-patch+json``.

    Only mutable fields are included — immutable-after-creation fields
    (``snssai``, ``dnn``, ``ueIpv4Addr``, ``ueIpv6Addr``, ``macAddr``,
    ``ipDomain``, ``sponsorInfo``) are excluded per the spec.
    """

    exterAppId: Optional[str] = None
    flowInfo: Optional[List[FlowInfo]] = Field(default=None, min_length=1)
    ethFlowInfo: Optional[List[Dict[str, Any]]] = Field(default=None, min_length=1)
    enEthFlowInfo: Optional[List[Dict[str, Any]]] = Field(default=None, min_length=1)
    qosReference: Optional[str] = None
    altQoSReferences: Optional[List[str]] = Field(default=None, min_length=1)
    altQosReqs: Optional[List[Dict[str, Any]]] = Field(default=None, min_length=1)
    disUeNotif: Optional[bool] = None
    usageThreshold: Optional[Dict[str, Any]] = None
    qosMonInfo: Optional[QosMonitoringInformation] = None
    directNotifInd: Optional[bool] = None
    notificationDestination: Optional[str] = None
    tscQosReq: Optional[TscQosRequirement] = None
    events: Optional[List[UserPlaneEvent]] = Field(default=None, min_length=1)

    model_config = {"populate_by_name": True, "validate_assignment": True}
