"""`POST /api/hooks/evolution/{channel_id}`: WhatsApp events from Evolution API (docs/channels.md,
«Приём»; docs/api.md, «Вебхуки»). Public — Evolution has no dashboard login — but every call
must carry the channel's own secret in `x-sim-webhook-secret` (checked in constant time):
404 for an unknown channel, 403 for a wrong secret. The body is never logged."""
from __future__ import annotations

from typing import Any

from litestar import Request, Response, post

from sim.api.auth import PUBLIC
from sim.api.context import ApiContext
from sim.api.errors import error_response
from sim.channels.whatsapp.evolution import SECRET_HEADER
from sim.channels.whatsapp.receiver import HookRefused

REFUSED = {403: "Неверный секрет вебхука", 404: "Канал не найден"}


@post("/hooks/evolution/{channel_id:int}", opt={PUBLIC: True}, status_code=200)
async def evolution_hook(channel_id: int, request: Request[Any, Any, Any],
                         ctx: ApiContext) -> Response[Any]:
    secret = request.headers.get(SECRET_HEADER, "")
    try:
        payload = await request.json()
    except Exception:
        payload = None                          # not JSON: verified, then nothing to read
    try:
        stored = await ctx.whatsapp.handle(channel_id, secret, payload)
    except HookRefused as exc:
        return error_response(exc.status, REFUSED.get(exc.status, "Отказано"))
    return Response({"ok": True, "stored": stored})


ROUTES = [evolution_hook]
