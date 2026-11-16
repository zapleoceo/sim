"""Every /api and /hooks request opens a trace: one `http`/`webhook` step (method, path, status,
duration, admin) and everything the request does (ingest, partner events...) under the same id.
The id is returned in `X-Trace-Id`. Cookies and headers are never logged; the query string is
masked; the body of a webhook or a POST/PUT/PATCH/DELETE is kept masked and cut
(`sim.services.request_body`), never for sign-in/OAuth, files only by type and size.
The log's own reads and health probes are not logged (they would drown it)."""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Response

from sim.services import request_body, tracing
from sim.services.tracing import Tracer, mask

TRACE_HEADER = "X-Trace-Id"
TRACED_PREFIXES = ("/api/", "/hooks/")
QUIET_GETS = ("/api/activity/log", "/api/activity/traces", "/api/live", "/api/ready", "/api/health",
              "/api/events")


def is_quiet(method: str, path: str) -> bool:
    return method in ("GET", "HEAD", "OPTIONS") and path.startswith(QUIET_GETS)


LINKED_PATH_PARAMS = ("client_id", "dialog_id", "channel_id")


def path_links(request: Request) -> dict[str, int]:
    """Ids from the matched route (`/api/dialogs/{dialog_id}/...`): an admin action on a client,
    dialog or channel shows up next to that client in the log."""
    params = request.path_params  # filled in by the router while the request ran
    return {k: int(v) for k in LINKED_PATH_PARAMS if str(v := params.get(k, "")).isdigit()}


def step_kind(path: str) -> str:
    return tracing.WEBHOOK if path.startswith("/hooks/") else tracing.HTTP


def install_request_tracing(app: FastAPI) -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, OAuthAccessFilter) for f in access.filters):
        access.addFilter(OAuthAccessFilter())
    @app.middleware("http")
    async def trace_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        container = getattr(request.app.state, "container", None)
        if container is None or not path.startswith(TRACED_PREFIXES):
            return await call_next(request)
        tracer = Tracer(container.factory, container.clock)
        quiet = is_quiet(request.method, path)
        async with tracer.scope() as ctx:
            started_at, start = container.clock(), time.perf_counter()
            detail: dict[str, Any] = {}
            if request.url.query and not path.startswith("/api/oauth/"):
                detail["query"] = mask(request.url.query, 300)
            if not quiet and request_body.wants_body(request.method, path):
                detail["request_body"] = await read_body(request)
            step = {"kind": step_kind(path), "name": f"{request.method} {path}",
                    "started_at": started_at, "detail": detail or None}
            try:
                response = await call_next(request)
            except Exception as exc:
                if not quiet:
                    await tracer.record(**step, status=tracing.ERROR, http_status=500,
                                        duration_ms=tracing.elapsed_ms(start), error=tracing.error_text(exc))
                raise
            if not quiet:
                await tracer.record(
                    **step, status=tracing.OK if response.status_code < 400 else tracing.ERROR,
                    http_status=response.status_code, duration_ms=tracing.elapsed_ms(start),
                    **path_links(request),
                )
        response.headers[TRACE_HEADER] = ctx.trace_id
        return response


async def read_body(request: Request) -> dict[str, Any]:
    """The body as the log keeps it. `request.body()` is cached by Starlette and replayed to the
    route by `call_next`; files, binary and oversized bodies are not read at all."""
    if request.url.path.startswith(request_body.PRIVATE_PREFIXES):
        return {"omitted": request_body.NOT_KEPT}
    content_type = request.headers.get("content-type", "")
    length = request.headers.get("content-length", "")
    size = int(length) if length.isdigit() else None
    if not request_body.is_text(content_type) or (size or 0) > request_body.READ_LIMIT:
        return request_body.omitted(content_type, size)
    try:
        raw = await request.body()
    except Exception:  # the client went away; the route will see it too
        return {"omitted": "тело не удалось прочитать"}
    if not raw:
        return {"omitted": "тело пустое"}
    return request_body.capture(raw, content_type)


class OAuthAccessFilter(logging.Filter):
    """Uvicorn access records otherwise retain the unmasked OAuth query string."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            if args[2].startswith("/api/oauth/"):
                record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True
