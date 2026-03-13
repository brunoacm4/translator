# coding: utf-8

"""
3GPP Common Data Types used by AsSessionWithQoS (TS 29.122 / TS 29.571)

Only the types the translator actually inspects or translates are fully
modelled.  Types that are simply stored and echoed back (e.g. SponsorInfo,
WebsockNotifConfig) are accepted as ``Dict[str, Any]`` in the subscription
model and not defined here.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  S-NSSAI  (TS 29.571)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Snssai(BaseModel):
    """Single Network Slice Selection Assistance Information."""
    sst: int = Field(..., ge=0, le=255, description="Slice/Service Type (0–255)")
    sd: Optional[str] = Field(
        default=None,
        pattern=r"^[A-Fa-f0-9]{6}$",
        description="Slice Differentiator — 6 hex digits, e.g. '000001'",
    )

    def to_hex(self) -> str:
        """Return the compact hex representation used by the SM (e.g. '01000001')."""
        if self.sd:
            return f"{self.sst:02X}{self.sd}"
        return f"{self.sst:02X}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  FlowInfo  (TS 29.122)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FlowInfo(BaseModel):
    """Describes one IP data flow that requires QoS."""
    flowId: int = Field(..., description="Unique flow identifier within the subscription")
    flowDescriptions: Optional[List[str]] = Field(
        default=None,
        description="SDF filter strings (IPFilterRule format); omitted for UL-only flows",
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  TSC QoS Requirement  (time-sensitive communication)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TscQosRequirement(BaseModel):
    """QoS requirements for time-sensitive communication."""
    reqGbrDl: Optional[str] = Field(default=None, description="Required GBR DL (BitRate string, e.g. '10 Mbps')")
    reqGbrUl: Optional[str] = Field(default=None, description="Required GBR UL (BitRate string)")
    reqMbrDl: Optional[str] = Field(default=None, description="Required MBR DL (BitRate string)")
    reqMbrUl: Optional[str] = Field(default=None, description="Required MBR UL (BitRate string)")
    maxTscBurstSize: Optional[int] = Field(default=None, description="Max TSC burst size (bytes)")
    req5Gsdelay: Optional[int] = Field(default=None, description="Required 5GS delay budget (ms)")
    priority: Optional[int] = Field(default=None, description="TSC priority level")
    tscaiTimeDom: Optional[int] = Field(default=None, description="TSCAI time domain")
    tscaiInputDl: Optional[Dict[str, Any]] = Field(default=None, description="TSCAI input container DL")
    tscaiInputUl: Optional[Dict[str, Any]] = Field(default=None, description="TSCAI input container UL")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  QoS Monitoring
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class QosMonitoringInformation(BaseModel):
    """QoS monitoring configuration attached to a subscription."""
    reqQosMonParams: List[str] = Field(..., min_length=1)
    repFreqs: List[str] = Field(..., min_length=1)
    repThreshDl: Optional[int] = None
    repThreshUl: Optional[int] = None
    repThreshRp: Optional[int] = None
    waitTime: Optional[int] = None
    repPeriod: Optional[int] = None


class QosMonitoringReport(BaseModel):
    """One QoS monitoring report entry."""
    ulDelays: Optional[List[int]] = None
    dlDelays: Optional[List[int]] = None
    rtDelays: Optional[List[int]] = None
    pdmf: Optional[bool] = Field(default=None, description="Packet delay measurement failure")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  User Plane Events & Notifications
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class UserPlaneEvent(str, Enum):
    """User-plane events the SCS/AS can subscribe to."""
    SESSION_TERMINATION = "SESSION_TERMINATION"
    LOSS_OF_BEARER = "LOSS_OF_BEARER"
    RECOVERY_OF_BEARER = "RECOVERY_OF_BEARER"
    RELEASE_OF_BEARER = "RELEASE_OF_BEARER"
    USAGE_REPORT = "USAGE_REPORT"
    FAILED_RESOURCES_ALLOCATION = "FAILED_RESOURCES_ALLOCATION"
    QOS_GUARANTEED = "QOS_GUARANTEED"
    QOS_NOT_GUARANTEED = "QOS_NOT_GUARANTEED"
    QOS_MONITORING = "QOS_MONITORING"
    SUCCESSFUL_RESOURCES_ALLOCATION = "SUCCESSFUL_RESOURCES_ALLOCATION"
    ACCESS_TYPE_CHANGE = "ACCESS_TYPE_CHANGE"
    PLMN_CHG = "PLMN_CHG"


class UserPlaneEventReport(BaseModel):
    """One event report inside a notification."""
    event: UserPlaneEvent
    accumulatedUsage: Optional[Dict[str, Any]] = None
    flowIds: Optional[List[int]] = None
    appliedQosRef: Optional[str] = None
    plmnId: Optional[Dict[str, Any]] = None
    qosMonReports: Optional[List[QosMonitoringReport]] = None
    ratType: Optional[str] = None


class UserPlaneNotificationData(BaseModel):
    """Payload sent to the notificationDestination callback."""
    transaction: str
    eventReports: List[UserPlaneEventReport] = Field(..., min_length=1)
