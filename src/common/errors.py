"""Canonical error handlers and responses conforming to the OpenAPI Error schema."""

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    field: str
    reason: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)
    request_id: str | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, str]] | None = None,
    request: Request | None = None,
) -> JSONResponse:
    """Return a standard RFC/contract error envelope."""
    req_id = request.headers.get("X-Request-ID") if request else None
    return JSONResponse(
        status_code=status_code,
        headers={"X-Request-ID": req_id} if req_id else {},
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or [],
                "request_id": req_id,
            }
        },
    )


def not_implemented_response(
    request: Request,
    endpoint_name: str,
    message: str = "This business operation is not implemented yet in Tier I baseline.",
) -> JSONResponse:
    """Return an explicit HTTP 501 Not Implemented response conforming to API contract."""
    return error_response(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        code="NOT_IMPLEMENTED",
        message=f"[{endpoint_name}] {message}",
        request=request,
    )


def register_canonical_error_handlers(app: FastAPI) -> None:
    """Register exception handlers on a FastAPI application to produce canonical Error envelopes."""

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        req_id = request.headers.get("X-Request-ID")
        details: list[dict[str, str]] = []
        for err in exc.errors():
            loc = ".".join(str(item) for item in err.get("loc", []) if item != "body")
            msg = err.get("msg", "Validation error")
            details.append({"field": loc or "body", "reason": msg})

        return JSONResponse(
            status_code=getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422),
            headers={"X-Request-ID": req_id} if req_id else {},
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request schema validation failed",
                    "details": details,
                    "request_id": req_id,
                }
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        req_id = request.headers.get("X-Request-ID")
        code_map = {
            400: "BAD_REQUEST",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "CONFLICT",
            422: "VALIDATION_ERROR",
            429: "RATE_LIMITED",
            500: "INTERNAL_ERROR",
            501: "NOT_IMPLEMENTED",
        }
        code = code_map.get(exc.status_code, "ERROR")
        return JSONResponse(
            status_code=exc.status_code,
            headers={"X-Request-ID": req_id} if req_id else {},
            content={
                "error": {
                    "code": code,
                    "message": str(exc.detail),
                    "details": [],
                    "request_id": req_id,
                }
            },
        )

    from sqlalchemy.exc import IntegrityError

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
        return error_response(
            status_code=status.HTTP_409_CONFLICT,
            code="CONFLICT",
            message="Operation failed due to concurrent modification or unique constraint conflict.",
            request=request,
        )
