"""Call recordings kept for `persona.call_recording_days` (docs/calls.md, «Запись звонка»).

Every hour `agent.recordings_tick` deletes the recordings older than their persona's days; the
persona page's «Удалить старые записи» does the same for one persona at once, optionally with a
shorter age. A deleted recording leaves the call, its transcript and its event: only the audio
goes. The files are deleted after the commit, so a failed delete leaves an orphan file, never a
call that points at nothing."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.db.models import Call, Chat, Persona
from sim.db.session import transaction
from sim.ports import MediaStoragePort

log = logging.getLogger(__name__)

MIN_DAYS = 1
RECORDING_PREFIX = "calls"
RECORDING_MIME = "audio/ogg"


async def expired_recordings(session: AsyncSession, now: datetime, *,
                             persona_id: int | None = None,
                             days: int | None = None) -> list[str]:
    """Clears the recordings older than the persona's days (or `days`) and returns their
    storage keys (the caller deletes the files after the commit)."""
    query = (select(Call, Persona.call_recording_days)
             .join(Chat, Chat.id == Call.chat_id).join(Persona, Persona.id == Chat.persona_id)
             .where(Call.recording_key.is_not(None)))
    if persona_id is not None:
        query = query.where(Persona.id == persona_id)
    keys: list[str] = []
    for call, persona_days in (await session.execute(query.with_for_update(of=Call))).all():
        keep = days if days is not None else max(MIN_DAYS, int(persona_days))
        if call.created_at < now - timedelta(days=keep):
            keys.append(str(call.recording_key))
            call.recording_key, call.recording_s = None, None
    return keys


async def delete_files(storage: MediaStoragePort, keys: list[str]) -> None:
    for key in keys:
        try:
            await storage.delete(key)
        except Exception:
            log.exception("recording %s not deleted from the storage", key)


async def purge_recordings(sessions: async_sessionmaker[AsyncSession],
                           storage: MediaStoragePort, now: datetime, *,
                           persona_id: int | None = None, days: int | None = None) -> int:
    """Deletes the expired recordings (one persona or all); returns how many."""
    async with transaction(sessions) as session:
        keys = await expired_recordings(session, now, persona_id=persona_id, days=days)
    await delete_files(storage, keys)
    return len(keys)
