"""Consistent error envelope and exception handlers.

Every error response uses the shape::

    {"error": {"code": ..., "message": ..., "detail": ...}}

with the correct HTTP status. ``ApiError`` is raised throughout the app and
converted by the handler registered in ``app.main``.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    """Application error with a machine code and HTTP status."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail


def _envelope(code: str, message: str, detail: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "detail": detail}}


async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.code, exc.message, exc.detail),
    )


async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(f"http_{exc.status_code}", str(exc.detail)),
    )


async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    # pydantic v2 attaches the original ValueError object in ``ctx``; strip
    # non-JSON-serializable fields so the response can be encoded.
    safe = []
    for err in exc.errors():
        safe.append({k: v for k, v in err.items() if k != "ctx" or _jsonable(v)})
    return JSONResponse(
        status_code=422,
        content=_envelope("validation_error", "Request validation failed", safe),
    )


def _jsonable(v: object) -> bool:
    """Cheap check: is ``v`` JSON-serializable without recursion?"""
    import json

    try:
        json.dumps(v)
    except (TypeError, ValueError):
        return False
    return True
