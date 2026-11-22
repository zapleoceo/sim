"""Call recordings (docs/calls.md, «Запись звонка», docs/api.md): listen to one in the chat
timeline, and «Удалить старые записи» on the persona page."""
from __future__ import annotations

from typing import Any

from litestar import Request, Response, get, post
from litestar.exceptions import NotFoundException
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.storage import MediaNotFound
from sim.api.context import ApiContext
from sim.api.files import file_response
from sim.api.queries import get_or_404, persona_or_404
from sim.api.schemas import PurgeRecordingsIn
from sim.db.models import Call
from sim.services.recordings import RECORDING_MIME, delete_files, expired_recordings

Json = dict[str, Any]


@get("/calls/{call_id:int}/recording")
async def call_recording(call_id: int, request: Request[Any, Any, Any], ctx: ApiContext,
                         db: AsyncSession) -> Response[bytes]:
    call = await get_or_404(db, Call, call_id, "Звонок")
    if not call.recording_key:
        raise NotFoundException(detail=f"У звонка {call_id} нет записи")
    try:
        data = await ctx.deps.storage.get(call.recording_key)
    except (MediaNotFound, FileNotFoundError, KeyError) as exc:
        raise NotFoundException(detail=f"Запись звонка {call_id} пропала из хранилища") from exc
    return file_response(data, mime=RECORDING_MIME, file_name=f"call-{call_id}.ogg",
                         range_header=request.headers.get("range"))


@post("/personas/{persona_id:int}/call-recordings/purge", status_code=200)
async def purge_call_recordings(persona_id: int, data: PurgeRecordingsIn, db: AsyncSession,
                                ctx: ApiContext) -> Json:
    """Deletes this persona's recordings older than `days` (default: her keeping period)."""
    await persona_or_404(db, persona_id)
    keys = await expired_recordings(db, ctx.deps.clock.now(), persona_id=persona_id,
                                    days=data.days)
    await db.commit()
    await delete_files(ctx.deps.storage, keys)
    return {"deleted": len(keys)}


ROUTES = [call_recording, purge_call_recordings]
