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
      GET    /operations/{operationId}                 → poll async op

    Each handler delegates to the registered BaseTranslatorApi implementation.
    Subclass discovery happens automatically via pkgutil/importlib.
"""

import importlib
import json
import pkgutil
from typing import List, Optional, Union

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
from app.models.operation import OperationAccepted, OperationStatus
from app.utils.idempotency import extract_idempotency_key


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


def _parse_ip_addrs(raw: Optional[str]) -> Optional[List[str]]:
    """
    Parse the ``ip-addrs`` query parameter into a list of addresses.

    TS 29.122 encodes it as a JSON array of ``IpAddr`` objects
    (``[{"ipv4Addr": "10.0.0.1"}]``). Plain strings inside the array and a
    comma-separated list are accepted too.
    """
    if not raw:
        return None
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        return [a.strip() for a in raw.split(",") if a.strip()]

    invalid = HTTPException(
        status_code=400,
        detail='ip-addrs must be a JSON array of IpAddr objects, e.g. [{"ipv4Addr":"10.0.0.1"}]',
    )
    if not isinstance(items, list):
        raise invalid
    addrs: List[str] = []
    for item in items:
        if isinstance(item, str):
            addrs.append(item)
        elif isinstance(item, dict) and (item.get("ipv4Addr") or item.get("ipv6Addr")):
            addrs.append(item.get("ipv4Addr") or item.get("ipv6Addr"))
        else:
            raise invalid
    return addrs


# ---------------------------------------------------------------------------
# POST /{scsAsId}/subscriptions
# Creates a new subscription → SM associate_slice (docs/adr/0006)
# ---------------------------------------------------------------------------
@router.post(
    "/{scsAsId}/subscriptions",
    responses={
        201: {"description": "Subscription created successfully"},
        202: {"description": "Duplicate request accepted (existing operation returned)"},
        400: {"description": "Bad request — invalid 3GPP payload; missing or unknown qosReference; snssai/dnn not matching the profile's slice"},
        403: {"description": "Forbidden — the UE already has a QoS session"},
        500: {"description": "Internal server error"},
        503: {"description": "Slice Manager unavailable"},
    },
    tags=["AS Session with Required QoS Subscriptions"],
    summary="Create a new AS session with QoS subscription",
    response_model=Union[AsSessionWithQoSSubscription, OperationAccepted],
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
) -> Union[AsSessionWithQoSSubscription, OperationAccepted]:
    """
    Receives a 3GPP AsSessionWithQoSSubscription, associates the UE with the
    slice its qosReference selects, stores the subscription, and returns the
    full resource with a Location header.
    """
    idem_key = extract_idempotency_key(request)
    result = await _impl().create_subscription(scsAsId, body, idem_key)

    if isinstance(result, OperationAccepted):
        response.status_code = status.HTTP_202_ACCEPTED
        return result

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
        description=(
            "UE IP addresses to filter by: a JSON array of IpAddr objects as in "
            'TS 29.122, e.g. [{"ipv4Addr":"10.0.0.1"}] (a comma-separated list '
            "is also accepted)"
        ),
    ),
) -> List[AsSessionWithQoSSubscription]:
    """Return all active subscriptions, optionally filtered by UE IP."""
    return await _impl().list_subscriptions(scsAsId, _parse_ip_addrs(ip_addrs))


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
# Delete subscription → SM dissociate_slice
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


# ---------------------------------------------------------------------------
# GET /operations/{operationId}
# Poll the status of an async operation (create subscription)
# ---------------------------------------------------------------------------
@router.get(
    "/operations/{operationId}",
    responses={
        200: {"description": "Operation status"},
        404: {"description": "Operation not found"},
    },
    tags=["Operations"],
    summary="Get async operation status",
    response_model=OperationStatus,
    response_model_by_alias=True,
    response_model_exclude_none=True,
)
async def get_operation(
    operationId: str = Path(..., description="Operation ID returned by the create endpoint"),
) -> OperationStatus:
    """Return the current status of a previously submitted operation."""
    return await _impl().get_operation(operationId)
