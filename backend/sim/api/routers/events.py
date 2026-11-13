"""`GET /api/events`: Server-Sent Events for the admin, so open pages refresh the moment a dialog
changes (see sim.services.live). Logged-in admins only; events carry dialog ids, never content."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from sim.api.deps import authed
from sim.services.live import HUB, LiveHub

router = APIRouter(tags=["events"])

HEARTBEAT_S = 15.0          # keeps nginx / Cloudflare from closing an idle stream
RETRY_MS = 3000             # browser reconnect delay after a drop


async def stream(request: Request, hub: LiveHub, heartbeat_s: float = HEARTBEAT_S) -> AsyncIterator[str]:
    yield f"retry: {RETRY_MS}\n\n"
    async with hub.subscribe() as queue:
        while not await request.is_disconnected():
            try:
                ids = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            yield f"event: dialogs\ndata: {json.dumps({'dialogs': ids})}\n\n"


@router.get("/api/events", dependencies=[Depends(authed)])
async def events(request: Request) -> StreamingResponse:
    return StreamingResponse(
        stream(request, HUB),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",        # nginx: pass every event through at once
            "Connection": "keep-alive",
        },
    )
