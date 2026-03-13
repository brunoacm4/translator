# coding: utf-8

"""
    Translator API — Router

    3GPP TS 29.122 AsSessionWithQoS endpoints.

    Routes:
      POST   /{scsAsId}/subscriptions                  → create
      GET    /{scsAsId}/subscriptions                  → list
      GET    /{scsAsId}/subscriptions/{subscriptionId} → get one
      PUT    /{scsAsId}/subscriptions/{subscriptionId} → full replace
      PATCH  /{scsAsId}/subscriptions/{subscriptionId} → partial update
      DELETE /{scsAsId}/subscriptions/{subscriptionId} → delete

    Each handler delegates to the registered BaseTranslatorApi implementation.
    Subclass discovery happens automatically via pkgutil/importlib.
"""

import importlib
import pkgutil
from typing import List, Optional

from fastapi import (
    APIRouter,
    Body,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)

from app.apis.translator_api_base import BaseTranslatorApi
import app.impl

from app.models.nef.subscription import (
    AsSessionWithQoSSubscription,
    AsSessionWithQoSSubscriptionPatch,
)


router = APIRouter()

# ---------------------------------------------------------------------------
# Auto-discover all implementation modules under app.impl so that any
# BaseTranslatorApi subclass is registered before the first request arrives.
# ---------------------------------------------------------------------------
ns_pkg = app.impl
for _, name, _ in pkgutil.iter_modules(ns_pkg.__path__, ns_pkg.__name__ + "."):
    importlib.import_module(name)


def _impl() -> BaseTranslatorApi:
    """Return the first registered implementation, or raise 500."""
    if not BaseTranslatorApi.subclasses:
        raise HTTPException(status_code=500, detail="No implementation registered")
    return BaseTranslatorApi.subclasses[0]()


# ---------------------------------------------------------------------------
# POST /{scsAsId}/subscriptions
# Creates a new subscription → SM create_slice + associate_slice
# ---------------------------------------------------------------------------
@router.post(
    "/{scsAsId}/subscriptions",
    responses={
        201: {"description": "Subscription created successfully"},
        400: {"description": "Bad request — invalid 3GPP payload"},
        403: {"description": "Forbidden"},
        500: {"description": "Internal server error"},
        503: {"description": "Slice Manager unavailable"},
    },
    tags=["AS Session with Required QoS Subscriptions"],
    summary="Create a new AS session with QoS subscription",
    response_model=AsSessionWithQoSSubscription,
    response_model_by_alias=True,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
async def create_subscription(
    request: Request,
    response: Response,
    scsAsId: str = Path(..., description="Identifier of the SCS/AS"),
    body: AsSessionWithQoSSubscription = Body(
        ..., description="AS session with QoS subscription resource"
    ),
) -> AsSessionWithQoSSubscription:
    """
    Receives a 3GPP AsSessionWithQoSSubscription, translates it into
    Slice Manager payloads, creates + associates the slice, stores the
    subscription, and returns the full resource with a Location header.
    """
    result = await _impl().create_subscription(scsAsId, body)

    # Set Location header per 3GPP spec
    sub_id = result.self_link or ""
    if "/" in sub_id:
        location = sub_id
    else:
        base = str(request.url).rstrip("/")
        location = f"{base}/{sub_id}"
    response.headers["Location"] = location

    return result


# ---------------------------------------------------------------------------
# GET /{scsAsId}/subscriptions
# List all active subscriptions for the SCS/AS
# ---------------------------------------------------------------------------
@router.get(
    "/{scsAsId}/subscriptions",
    responses={
        200: {"description": "OK"},
        400: {"description": "Bad request"},
        404: {"description": "SCS/AS not found"},
    },
    tags=["AS Session with Required QoS Subscriptions"],
    summary="List all active subscriptions",
    response_model=List[AsSessionWithQoSSubscription],
    response_model_by_alias=True,
    response_model_exclude_none=True,
)
async def list_subscriptions(
    scsAsId: str = Path(..., description="Identifier of the SCS/AS"),
    ip_addrs: Optional[str] = Query(
        default=None,
        alias="ip-addrs",
        description="Comma-separated UE IP addresses to filter by",
    ),
) -> List[AsSessionWithQoSSubscription]:
    """Return all active subscriptions, optionally filtered by UE IP."""
    addrs = [a.strip() for a in ip_addrs.split(",")] if ip_addrs else None
    return await _impl().list_subscriptions(scsAsId, addrs)


# ---------------------------------------------------------------------------
# GET /{scsAsId}/subscriptions/{subscriptionId}
# Read a single subscription
# ---------------------------------------------------------------------------
@router.get(
    "/{scsAsId}/subscriptions/{subscriptionId}",
    responses={
        200: {"description": "OK"},
        404: {"description": "Subscription not found"},
    },
    tags=["Individual AS Session with Required QoS Subscription"],
    summary="Read an active subscription",
    response_model=AsSessionWithQoSSubscription,
    response_model_by_alias=True,
    response_model_exclude_none=True,
)
async def get_subscription(
    scsAsId: str = Path(..., description="Identifier of the SCS/AS"),
    subscriptionId: str = Path(..., description="Identifier of the subscription"),
) -> AsSessionWithQoSSubscription:
    """Return one subscription resource."""
    return await _impl().get_subscription(scsAsId, subscriptionId)


# ---------------------------------------------------------------------------
# PUT /{scsAsId}/subscriptions/{subscriptionId}
# Full replacement → SM change_slice
# ---------------------------------------------------------------------------
@router.put(
    "/{scsAsId}/subscriptions/{subscriptionId}",
    responses={
        200: {"description": "Subscription updated"},
        204: {"description": "Subscription updated (no content)"},
        404: {"description": "Subscription not found"},
    },
    tags=["Individual AS Session with Required QoS Subscription"],
    summary="Replace an existing subscription",
    response_model=AsSessionWithQoSSubscription,
    response_model_by_alias=True,
    response_model_exclude_none=True,
)
async def update_subscription(
    scsAsId: str = Path(..., description="Identifier of the SCS/AS"),
    subscriptionId: str = Path(..., description="Identifier of the subscription"),
    body: AsSessionWithQoSSubscription = Body(
        ..., description="Full subscription replacement"
    ),
) -> AsSessionWithQoSSubscription:
    """Replace the entire subscription resource and push changes to the SM."""
    return await _impl().update_subscription(scsAsId, subscriptionId, body)


# ---------------------------------------------------------------------------
# PATCH /{scsAsId}/subscriptions/{subscriptionId}
# Partial update (merge-patch+json) → SM change_slice
# ---------------------------------------------------------------------------
@router.patch(
    "/{scsAsId}/subscriptions/{subscriptionId}",
    responses={
        200: {"description": "Subscription modified"},
        204: {"description": "Subscription modified (no content)"},
        404: {"description": "Subscription not found"},
    },
    tags=["Individual AS Session with Required QoS Subscription"],
    summary="Modify an existing subscription",
    response_model=AsSessionWithQoSSubscription,
    response_model_by_alias=True,
    response_model_exclude_none=True,
)
async def patch_subscription(
    scsAsId: str = Path(..., description="Identifier of the SCS/AS"),
    subscriptionId: str = Path(..., description="Identifier of the subscription"),
    body: AsSessionWithQoSSubscriptionPatch = Body(
        ..., description="Partial subscription update (merge-patch+json)"
    ),
) -> AsSessionWithQoSSubscription:
    """Partially update the subscription and push changes to the SM."""
    return await _impl().patch_subscription(scsAsId, subscriptionId, body)


# ---------------------------------------------------------------------------
# DELETE /{scsAsId}/subscriptions/{subscriptionId}
# Delete subscription → SM delete_slice
# ---------------------------------------------------------------------------
@router.delete(
    "/{scsAsId}/subscriptions/{subscriptionId}",
    responses={
        204: {"description": "Subscription deleted"},
        200: {"description": "Subscription deleted (with notification data)"},
        404: {"description": "Subscription not found"},
    },
    tags=["Individual AS Session with Required QoS Subscription"],
    summary="Delete a subscription",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_subscription(
    scsAsId: str = Path(..., description="Identifier of the SCS/AS"),
    subscriptionId: str = Path(..., description="Identifier of the subscription"),
) -> Response:
    """Delete the subscription and release SM slice resources."""
    await _impl().delete_subscription(scsAsId, subscriptionId)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
