"""Domain errors → HTTP responses, in one place instead of a try/except in every route.

The body is the same `{"detail": "<text>"}` an HTTPException gives. Starlette picks the handler of
the most specific class in the exception's MRO, so a subclass (ContentConflict) can map to another
status than its base (ContentError)."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from sim.ports.llm import LLMError
from sim.services.channels import ChannelError
from sim.services.content import ContentConflict, ContentError
from sim.services.conversation import ConversationError
from sim.services.persona_media import MediaNotFound, MediaRejected, MediaTooLarge
from sim.services.preview_media import AttachmentError

STATUS_BY_ERROR: dict[type[Exception], int] = {
    ChannelError: 422,
    ContentError: 422,
    ContentConflict: 409,
    ConversationError: 422,
    AttachmentError: 422,
    MediaRejected: 422,
    MediaTooLarge: 413,
    MediaNotFound: 404,
}
BROKER_STATUS = 502


async def domain_error(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=STATUS_BY_ERROR[type_in(exc)])


async def broker_error(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": f"Broker error: {exc}"}, status_code=BROKER_STATUS)


def type_in(exc: Exception) -> type[Exception]:
    """The most specific mapped class of the exception."""
    return next(cls for cls in type(exc).__mro__ if cls in STATUS_BY_ERROR)


def install_error_handlers(app: FastAPI) -> None:
    for error in STATUS_BY_ERROR:
        app.add_exception_handler(error, domain_error)
    app.add_exception_handler(LLMError, broker_error)
