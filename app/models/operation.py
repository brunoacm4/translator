# coding: utf-8

from __future__ import annotations

from typing import Optional

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
    sm_request_id: Optional[str] = Field(default=None, alias="smRequestId")
    error: Optional[str] = None
    created_at: Optional[str] = Field(default=None, alias="createdAt")
    updated_at: Optional[str] = Field(default=None, alias="updatedAt")

    model_config = {
        "populate_by_name": True,
        "validate_assignment": True,
    }
