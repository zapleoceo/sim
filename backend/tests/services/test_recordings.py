"""Call recordings are kept for the persona's days (sim/services/recordings.py, docs/calls.md,
«Запись звонка»): older ones lose their audio (the call, its transcript and its event stay),
the files go after the commit; the hourly tick and «Удалить старые записи» of one persona."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.db.models import Call, Chat
from sim.domain.enums import CallDirection, CallStatus
from sim.services.deps import Deps, reset_deps, set_deps
from sim.services.recordings import (
    RECORDING_MIME,
    RECORDING_PREFIX,
    delete_files,
    expired_recordings,
    purge_recordings,
)
from sim.tasks import agent
from tests.fakes.ai import MemoryStorage
from tests.services.conftest import Fakes
from tests.services.factories import fresh, make_chat, make_persona


async def recorded(db: AsyncSession, chat: Chat, clock: FixedClock, storage: MemoryStorage, *,
                   days_ago: float) -> Call:
    key = await storage.put(b"OggS", mime=RECORDING_MIME, prefix=RECORDING_PREFIX)
    call = Call(chat_id=chat.id, direction=CallDirection.IN, status=CallStatus.ENDED,
                transcript=[{"who": "client", "text": "алло", "t": 0}], recording_key=key,
                recording_s=12.5, created_at=clock.now() - timedelta(days=days_ago))
    db.add(call)
    await db.flush()
    return call


async def test_recordings_older_than_the_persona_days_lose_their_audio(
        db: AsyncSession, clock: FixedClock) -> None:
    storage = MemoryStorage()
    chat = await make_chat(db, await make_persona(db, call_recording_days=7))
    old = await recorded(db, chat, clock, storage, days_ago=8)
    new = await recorded(db, chat, clock, storage, days_ago=6)
    old_key = old.recording_key

    assert await expired_recordings(db, clock.now()) == [old_key]

    old, new = await fresh(db, old), await fresh(db, new)
    assert (old.recording_key, old.recording_s) == (None, None)
    assert old.transcript and old.status == CallStatus.ENDED      # only the audio goes
    assert new.recording_key is not None and new.recording_s == 12.5
    assert old_key in storage.files                     # the caller deletes after the commit


async def test_one_persona_and_a_shorter_age(db: AsyncSession, clock: FixedClock) -> None:
    storage = MemoryStorage()
    mine = await make_chat(db, await make_persona(db, name="Марина"))
    other = await make_chat(db, await make_persona(db, name="Оля"))
    week = await recorded(db, mine, clock, storage, days_ago=7)
    today = await recorded(db, mine, clock, storage, days_ago=0.1)
    theirs = await recorded(db, other, clock, storage, days_ago=7)
    week_key = week.recording_key

    keys = await expired_recordings(db, clock.now(), persona_id=mine.persona_id, days=3)

    assert keys == [week_key]
    assert (await fresh(db, today)).recording_key is not None
    assert (await fresh(db, theirs)).recording_key is not None
    # 0 days = every recording of hers.
    everything = await expired_recordings(db, clock.now(), persona_id=mine.persona_id, days=0)
    assert len(everything) == 1 and (await fresh(db, today)).recording_key is None


async def test_purge_deletes_the_files_after_the_commit(db: AsyncSession, deps: Deps,
                                                        fakes: Fakes,
                                                        clock: FixedClock) -> None:
    chat = await make_chat(db, await make_persona(db, call_recording_days=1))
    old = await recorded(db, chat, clock, fakes.storage, days_ago=2)
    new = await recorded(db, chat, clock, fakes.storage, days_ago=0.5)
    old_key, new_key = old.recording_key, new.recording_key

    deleted = await purge_recordings(deps.sessions, fakes.storage, clock.now())

    assert deleted == 1
    assert old_key not in fakes.storage.files and new_key in fakes.storage.files
    assert (await fresh(db, old)).recording_key is None


async def test_the_hourly_tick_purges_every_persona(db: AsyncSession, deps: Deps, fakes: Fakes,
                                                    clock: FixedClock) -> None:
    chat = await make_chat(db, await make_persona(db, call_recording_days=30))
    old = await recorded(db, chat, clock, fakes.storage, days_ago=31)
    set_deps(deps)
    try:
        await agent.recordings_tick(0)
    finally:
        reset_deps()
    assert fakes.storage.files == {}
    assert (await fresh(db, old)).recording_key is None


class BrokenStorage(MemoryStorage):
    async def delete(self, key: str) -> None:
        if key == "calls/broken":
            raise OSError("disk gone")
        await super().delete(key)


async def test_a_failed_delete_only_leaves_an_orphan_file() -> None:
    storage = BrokenStorage()
    storage.files.update({"calls/broken": b"1", "calls/ok": b"2"})
    await delete_files(storage, ["calls/broken", "calls/ok"])
    assert storage.files == {"calls/broken": b"1"}
