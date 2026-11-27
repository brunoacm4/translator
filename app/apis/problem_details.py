# coding: utf-8

"""
3GPP error responses.

TS 29.122 (via TS 29.571 ``ProblemDetails``) returns errors as
``application/problem+json`` with ``title``, ``status``, ``detail`` and, for
malformed requests, ``invalidParams``. These handlers turn every error the app
raises into that shape. Request validation errors become ``400 Bad Request``:
the 3GPP API defines no ``422``.

Every rejected request (``4xx``) is also logged with its method, path, status
and reason, never with its body: the body may carry personal data such as the
UE address. The reasons come from the error details and validation messages,
which carry no request values. ``5xx`` errors come from SM failures, which the
SM client already logs.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.impl.session_core import SmFailed, SmUnavailable

PROBLEM_JSON = "application/problem+json"

logger = logging.getLogger(__name__)


def problem_response(
    status: int,
    detail: Optional[str] = None,
    invalid_params: Optional[List[Dict[str, str]]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> JSONResponse:
    """Build a ``ProblemDetails`` response."""
    try:
        title = HTTPStatus(status).phrase
    except ValueError:
        title = "Error"
    body: Dict[str, Any] = {"title": title, "status": status}
    if detail:
        body["detail"] = detail
    if invalid_params:
        body["invalidParams"] = invalid_params
    return JSONResponse(
        status_code=status, content=body, media_type=PROBLEM_JSON, headers=headers
    )


def _param_name(loc: tuple) -> str:
    """``("body", "tscQosReq", "reqGbrUl")`` → ``"tscQosReq.reqGbrUl"``."""
    parts = [str(p) for p in loc if p not in ("body", "query", "path", "header")]
    return ".".join(parts) or "body"


def _log_rejection(request: Request, status: int, reason: str) -> None:
    """Log a rejected request with its reason, without its body."""
    logger.warning(
        "Request rejected  method=%s  path=%s  status=%d  reason=%s",
        request.method,
        request.url.path,
        status,
        reason,
    )


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else None
    if 400 <= exc.status_code < 500:
        _log_rejection(request, exc.status_code, detail or "-")
    return problem_response(exc.status_code, detail, headers=getattr(exc, "headers", None))


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    invalid_params = [
        {"param": _param_name(tuple(err.get("loc", ()))), "reason": err.get("msg", "invalid")}
        for err in exc.errors()
    ]
    _log_rejection(
        request,
        400,
        "invalid " + "; ".join(f"{p['param']}: {p['reason']}" for p in invalid_params),
    )
    return problem_response(400, "The request is not valid", invalid_params)


async def _sm_unavailable_handler(_: Request, exc: SmUnavailable) -> JSONResponse:
    return problem_response(503, str(exc))


async def _sm_failed_handler(_: Request, exc: SmFailed) -> JSONResponse:
    return problem_response(502, str(exc))


def register_problem_handlers(app: FastAPI) -> None:
    """Install the ``ProblemDetails`` exception handlers on *app*, and document
    them in its OpenAPI schema. A failed SM call answers the same in both
    APIs: 503 when the circuit breaker is open, 502 otherwise."""
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(SmUnavailable, _sm_unavailable_handler)
    app.add_exception_handler(SmFailed, _sm_failed_handler)
    _document_problem_details(app)


_PROBLEM_DETAILS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "description": "3GPP ProblemDetails (TS 29.571), sent as application/problem+json.",
    "properties": {
        "title": {"type": "string"},
        "status": {"type": "integer"},
        "detail": {"type": "string"},
        "invalidParams": {
            "type": "array",
            "items": {"$ref": "#/components/schemas/InvalidParam"},
            "minItems": 1,
        },
    },
}

_INVALID_PARAM_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["param"],
    "properties": {"param": {"type": "string"}, "reason": {"type": "string"}},
}


def _document_problem_details(app: FastAPI) -> None:
    """Make the generated OpenAPI match the handlers above: FastAPI's ``422``
    becomes ``400``, and every error response carries ``ProblemDetails``."""
    generate = app.openapi

    def openapi() -> Dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = generate()
        problem = {PROBLEM_JSON: {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}}
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if not isinstance(operation, dict):
                    continue
                responses = operation.get("responses", {})
                if responses.pop("422", None) is not None:
                    responses.setdefault("400", {"description": "Bad request"})
                for code, response in responses.items():
                    if code[0] in "45":
                        response["content"] = problem
        schemas = schema.setdefault("components", {}).setdefault("schemas", {})
        schemas.pop("HTTPValidationError", None)
        schemas.pop("ValidationError", None)
        schemas["ProblemDetails"] = _PROBLEM_DETAILS_SCHEMA
        schemas["InvalidParam"] = _INVALID_PARAM_SCHEMA
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
