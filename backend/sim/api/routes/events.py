"""`GET /api/events`: Server-Sent Events, one per journal event (docs/api.md). Events carry
only ids and the kind; the dashboard re-reads the timeline and the chat list."""
from __future__ import annotations

from litestar import get
from litestar.response import Stream

from sim.api.context import ApiContext
from sim.api.live import sse_stream

SSE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",      # nginx: pass every event through at once
}


@get("/events")
async def events(ctx: ApiContext) -> Stream:
    return Stream(sse_stream(ctx.hub), media_type="text/event-stream", headers=SSE_HEADERS)


ROUTES = [events]
