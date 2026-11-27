# coding: utf-8

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class OperationAccepted(BaseModel):
    """Immediate 202 response returned when an operation is enqueued."""

    operation_id: str = Field(alias="operationId")
    status: str
    subscription_id: Optional[str] = Field(default=None, alias="subscriptionId")

    model_config = {
        "populate_by_name": True,
        "validate_assignment": True,
    }


class OperationStatus(BaseModel):
    """Full operation record returned by ``GET /operations/{operationId}``."""

    operation_id: str = Field(alias="operationId")
    status: str
    subscription_id: Optional[str] = Field(default=None, alias="subscriptionId")
    sm_slice_id: Optional[str] = Field(default=None, alias="smSliceId")
    # Every SM request_id of the operation (associate_slice, or change_slice for a QoS change)
    sm_request_ids: List[str] = Field(default_factory=list, alias="smRequestIds")
    error: Optional[str] = None
    created_at: Optional[str] = Field(default=None, alias="createdAt")
    updated_at: Optional[str] = Field(default=None, alias="updatedAt")

    model_config = {
        "populate_by_name": True,
        "validate_assignment": True,
    }
