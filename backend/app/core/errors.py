"""Consistent API error model."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.core.logging import get_logger

log = get_logger(__name__)


class AppError(Exception):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, details: Any = None,
                 status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details


class NotFound(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class Conflict(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class Unauthorized(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class Forbidden(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class TooManyRequests(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


class ValidationFailed(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "validation_error"


def _body(code: str, message: str, details: Any = None, request_id: str | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details, "request_id": request_id}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code,
                            content=_body(exc.code, exc.message, exc.details, getattr(request.state, "request_id", None)))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(status_code=422, content=_body("validation_error", "Request validation failed", details,
                                                           getattr(request.state, "request_id", None)))

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError) -> JSONResponse:
        log.warning("integrity error: %s", type(exc.orig).__name__)
        return JSONResponse(status_code=409, content=_body("conflict", "The record conflicts with existing data",
                                                           None, getattr(request.state, "request_id", None)))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return JSONResponse(status_code=500, content=_body("internal_error", "An unexpected error occurred", None,
                                                           getattr(request.state, "request_id", None)))
