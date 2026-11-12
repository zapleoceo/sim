"""Every /api and /hooks request opens a trace: one `http`/`webhook` step (method, path, status,
duration, admin) and everything the request does (ingest, partner events...) under the same id.
The id is returned in `X-Trace-Id`. Bodies, cookies and headers are never logged; the query
string is masked. The log's own reads and health probes are not logged (they would drown it)."""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from sim.services import tracing
from sim.services.tracing import Tracer, mask

TRACE_HEADER = "X-Trace-Id"
TRACED_PREFIXES = ("/api/", "/hooks/")
QUIET_GETS = ("/api/activity/log", "/api/activity/traces", "/api/live", "/api/ready", "/api/health")


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
            step = {"kind": step_kind(path), "name": f"{request.method} {path}",
                    "started_at": started_at, "detail": {"query": mask(request.url.query, 300)} if request.url.query else None}
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
