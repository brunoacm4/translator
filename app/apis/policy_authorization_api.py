# coding: utf-8

"""
    Policy Authorization API — Router

    3GPP TS 29.514 Npcf_PolicyAuthorization endpoints (N5), minimal scope
    (docs/adr/0003). Mounted under ``/npcf-policyauthorization/v1``.

    Routes:
      POST   /app-sessions                        → create
      PATCH  /app-sessions/{appSessionId}         → modify (merge-patch+json)
      POST   /app-sessions/{appSessionId}/delete  → delete

    Other methods on these paths answer 405.
"""

from typing import Optional

from fastapi import APIRouter, Body, Path, Request, Response, status
from fastapi.responses import JSONResponse

from app.impl.policy_authorization_service import PolicyAuthorizationService
from app.models.pcf.app_session import (
    AppSessionContext,
    AppSessionContextUpdateDataPatch,
    EventsSubscReqData,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /app-sessions
# Creates an Individual Application Session Context → core start
# ---------------------------------------------------------------------------
@router.post(
    "/app-sessions",
    responses={
        201: {"description": "Successful creation of the resource"},
        400: {"description": "Bad request"},
        403: {"description": "Forbidden — missing, unknown or conflicting qosReference; sliceInfo/dnn not matching the profile's slice; the UE already has a session"},
        404: {"description": "Not found — unknown UE"},
        502: {"description": "Slice Manager error"},
        503: {"description": "Slice Manager unavailable"},
    },
    tags=["Application Sessions (Collection)"],
    summary="Creates a new Individual Application Session Context resource",
    response_model=AppSessionContext,
    status_code=status.HTTP_201_CREATED,
)
async def post_app_sessions(
    request: Request,
    body: AppSessionContext = Body(..., description="Contains the information for the creation the resource"),
) -> JSONResponse:
    """Authorize the session: start it in the core, return it with its Location."""
    location, context = await PolicyAuthorizationService().create(body, str(request.url))
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=context,
        headers={"Location": location},
    )


# ---------------------------------------------------------------------------
# PATCH /app-sessions/{appSessionId}
# Modifies the session (merge-patch+json) → core change
# ---------------------------------------------------------------------------
@router.patch(
    "/app-sessions/{appSessionId}",
    responses={
        200: {"description": "Successful modification; the resource is returned"},
        400: {"description": "Bad request"},
        403: {"description": "Forbidden — missing, unknown or conflicting qosReference; sliceInfo/dnn not matching the profile's slice"},
        404: {"description": "Application session not found"},
    },
    tags=["Individual Application Session Context (Document)"],
    summary="Modifies an existing Individual Application Session Context",
    response_model=AppSessionContext,
)
async def mod_app_session(
    appSessionId: str = Path(..., description="String identifying the resource"),
    body: AppSessionContextUpdateDataPatch = Body(..., description="Modification of the resource"),
) -> JSONResponse:
    """Apply the merge patch and push QoS changes to the core."""
    context = await PolicyAuthorizationService().modify(appSessionId, body)
    return JSONResponse(status_code=status.HTTP_200_OK, content=context)


# ---------------------------------------------------------------------------
# POST /app-sessions/{appSessionId}/delete
# Deletes the session → core stop
# ---------------------------------------------------------------------------
@router.post(
    "/app-sessions/{appSessionId}/delete",
    responses={
        204: {"description": "The deletion is confirmed without returning additional data"},
        404: {"description": "Application session not found"},
    },
    tags=["Individual Application Session Context (Document)"],
    summary="Deletes an existing Individual Application Session Context",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_app_session(
    appSessionId: str = Path(..., description="String identifying the resource"),
    body: Optional[EventsSubscReqData] = Body(
        default=None, description="Deletion of the resource, req notification (not used yet)"
    ),
) -> Response:
    """Stop the session in the core and remove the resource."""
    await PolicyAuthorizationService().delete(appSessionId)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
