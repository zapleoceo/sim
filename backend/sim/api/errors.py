"""Every error leaves the API as `{"error": "text for a human"}` with a 4xx/5xx code."""
from __future__ import annotations

import logging
from typing import Any

from litestar import Request, Response
from litestar.exceptions import HTTPException, ValidationException

from sim.ports import PortError

logger = logging.getLogger(__name__)

INTERNAL_ERROR = "Внутренняя ошибка сервера"


def error_response(status_code: int, message: str,
                   headers: dict[str, str] | None = None) -> Response[dict[str, str]]:
    return Response({"error": message}, status_code=status_code, headers=headers)


def validation_message(exc: ValidationException) -> str:
    """`Validation failed for POST /api/x: field: reason` from Litestar's `extra` details."""
    extra = exc.extra if isinstance(exc.extra, list) else [exc.extra] if exc.extra else []
    reasons = []
    for item in extra:
        if isinstance(item, dict):
            key, message = item.get("key"), item.get("message", "")
            reasons.append(f"{key}: {message}" if key else str(message))
        else:
            reasons.append(str(item))
    return f"{exc.detail}: {'; '.join(reasons)}" if reasons else str(exc.detail)


def http_error(_: Request[Any, Any, Any], exc: HTTPException) -> Response[dict[str, str]]:
    message = validation_message(exc) if isinstance(exc, ValidationException) else exc.detail
    return error_response(exc.status_code, message, dict(exc.headers or {}) or None)


def port_error(_: Request[Any, Any, Any], exc: PortError) -> Response[dict[str, str]]:
    """An outside service (Broker, storage) failed: the request was fine, the upstream was not."""
    return error_response(502, str(exc) or "Внешний сервис недоступен")


def internal_error(request: Request[Any, Any, Any], exc: Exception) -> Response[dict[str, str]]:
    logger.error("unhandled error in %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(500, INTERNAL_ERROR)


EXCEPTION_HANDLERS: dict[type[Exception], Any] = {
    HTTPException: http_error,
    PortError: port_error,
    Exception: internal_error,
}
