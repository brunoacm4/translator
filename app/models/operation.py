# coding: utf-8

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class OperationAccepted(BaseModel):
    operation_id: str = Field(alias="operationId")
    status: str
    subscription_id: Optional[str] = Field(default=None, alias="subscriptionId")

    model_config = {
        "populate_by_name": True,
        "validate_assignment": True,
    }
