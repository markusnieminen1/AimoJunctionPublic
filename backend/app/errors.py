import logging
from contextvars import ContextVar
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from ulid import ULID

from app import clock

logger = logging.getLogger(__name__)

request_id_var: ContextVar[str] = ContextVar("request_id", default="")

_HTTP_STATUS_TO_CODE: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    503: "UPSTREAM_UNAVAILABLE",
}


class AppError(Exception):
    """Domain/API error serialized with the contract error envelope."""

    def __init__(
        self,
        code: str,
        status_code: int,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.message = message
        self.details = details if details is not None else {}
        super().__init__(message)


def get_request_id(request: Request | None = None) -> str:
    """Resolve the active request_id from request state or contextvars."""
    if request is not None:
        state_id = getattr(request.state, "request_id", None)
        if isinstance(state_id, str) and state_id:
            return state_id
    ctx_id = request_id_var.get()
    if ctx_id:
        return ctx_id
    generated = f"req_{ULID()}"
    request_id_var.set(generated)
    if request is not None:
        request.state.request_id = generated
    return generated


def _build_error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    req_id = get_request_id(request)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details if details is not None else {},
            },
            "request_id": req_id,
        },
        headers={
            "X-Request-ID": req_id,
            "X-Server-Time": clock.now_iso(),
        },
    )


def _http_status_code(status_code: int) -> str:
    if status_code in _HTTP_STATUS_TO_CODE:
        return _HTTP_STATUS_TO_CODE[status_code]
    if 400 <= status_code < 500:
        return "BAD_REQUEST"
    return "INTERNAL"


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Serialize AppError using the contract error envelope."""
    return _build_error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


_SENSITIVE_FIELDS = frozenset({"password", "agent_secret"})


def _sanitize_validation_errors(
    raw_errors: list[Any],
) -> tuple[list[Any], str | None]:
    """Redact sensitive inputs in validation errors and extract the first body field."""
    sanitized: list[Any] = []
    first_body_field: str | None = None

    for item in raw_errors:
        if not isinstance(item, dict):
            sanitized.append(item)
            continue
        entry = dict(item)
        loc = entry.get("loc")
        if isinstance(loc, (list, tuple)):
            loc_strs = [str(part) for part in loc]
            if first_body_field is None and len(loc_strs) >= 2 and loc_strs[0] == "body":
                first_body_field = loc_strs[1]
            if "input" in entry and any(part.lower() in _SENSITIVE_FIELDS for part in loc_strs):
                entry["input"] = "[REDACTED]"
        sanitized.append(entry)

    return sanitized, first_body_field


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Serialize FastAPI RequestValidationError as 422 VALIDATION_ERROR."""
    encoded_errors = jsonable_encoder(
        exc.errors(),
        custom_encoder={Exception: lambda e: str(e)},
    )
    safe_errors, first_field = _sanitize_validation_errors(
        encoded_errors if isinstance(encoded_errors, list) else []
    )
    details: dict[str, Any] = {"errors": safe_errors}
    if first_field is not None:
        details["field"] = first_field
    return _build_error_response(
        request,
        status_code=422,
        code="VALIDATION_ERROR",
        message="Request validation failed",
        details=details,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Serialize FastAPI/Starlette HTTPException using the contract error envelope."""
    detail = exc.detail
    if exc.status_code == 404 and detail == "Not Found":
        message = f"No such endpoint: {request.method} {request.url.path}"
        details: dict[str, Any] = {"path": request.url.path}
    elif exc.status_code == 405 and detail == "Method Not Allowed":
        message = f"Method {request.method} not allowed for {request.url.path}"
        details = {"path": request.url.path, "method": request.method}
    else:
        message = detail if isinstance(detail, str) else "Request error"
        details = {} if isinstance(detail, str) else {"detail": jsonable_encoder(detail)}
    return _build_error_response(
        request,
        status_code=exc.status_code,
        code=_http_status_code(exc.status_code),
        message=message,
        details=details,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log full traceback with request_id and return 500 INTERNAL."""
    req_id = get_request_id(request)
    token = request_id_var.set(req_id)
    try:
        logger.exception("Unhandled exception during request: %s", exc)
    finally:
        request_id_var.reset(token)
    return _build_error_response(
        request,
        status_code=500,
        code="INTERNAL",
        message="Internal server error",
        details={},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all standard contract exception handlers to the FastAPI app."""
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
