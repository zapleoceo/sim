"""Call recordings on the dashboard (docs/calls.md, «Запись звонка», docs/api.md): the call in
the timeline links its recording, the recording is served (also by range), and «Удалить старые
записи» on the persona page; the persona's recording settings."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.db.models import Call, ChatEvent, Persona
from sim.domain.enums import CallDirection, CallStatus, EventKind
from tests.api.fakes import FakeDeps
from tests.api.helpers import ApiClient, World, pp

OGG = b"OggS" + b"\x01" * 60


async def a_call(db: AsyncSession, deps: FakeDeps, world: World, clock: FixedClock, *,
                 days_ago: float = 0, recorded: bool = True) -> Call:
    key = await deps.storage.put(OGG, mime="audio/ogg", prefix="calls") if recorded else None
    call = Call(chat_id=world.chat_id, direction=CallDirection.IN, status=CallStatus.ENDED,
                duration_s=42.0, transcript=[], recording_key=key,
                recording_s=41.5 if recorded else None,
                created_at=clock.now() - timedelta(days=days_ago))
    db.add(call)
    await db.flush()
    db.add(ChatEvent(chat_id=world.chat_id, kind=EventKind.CALL, actor="client",
                     in_context=True, call_id=call.id, data={"status": "ended"}))
    await db.commit()
    return call


async def test_the_timeline_links_the_recording(api: ApiClient, world: World, db: AsyncSession,
                                                deps: FakeDeps, clock: FixedClock) -> None:
    recorded = await a_call(db, deps, world, clock)
    silent = await a_call(db, deps, world, clock, recorded=False)
    items = (await api.get(f"/api/chats/{world.chat_id}/timeline")).json()["items"]
    calls = {item["call"]["id"]: item["call"] for item in items if "call" in item}
    assert calls[recorded.id]["recording_url"] == f"/api/calls/{recorded.id}/recording"
    assert calls[recorded.id]["recording_s"] == 41.5
    assert (calls[silent.id]["recording_url"], calls[silent.id]["recording_s"]) == (None, None)


async def test_the_recording_is_served(api: ApiClient, world: World, db: AsyncSession,
                                       deps: FakeDeps, clock: FixedClock) -> None:
    call = await a_call(db, deps, world, clock)
    whole = await api.get(f"/api/calls/{call.id}/recording")
    assert whole.status_code == 200
    assert whole.content == OGG and whole.headers["content-type"].startswith("audio/ogg")
    part = await api.get(f"/api/calls/{call.id}/recording", headers={"Range": "bytes=0-3"})
    assert part.status_code == 206 and part.content == b"OggS"

    silent = await a_call(db, deps, world, clock, recorded=False)
    assert (await api.get(f"/api/calls/{silent.id}/recording")).status_code == 404
    assert (await api.get("/api/calls/999999/recording")).status_code == 404
    deps.storage.files.clear()                          # the file is gone from the storage
    assert (await api.get(f"/api/calls/{call.id}/recording")).status_code == 404


async def test_purge_old_recordings_of_one_persona(api: ApiClient, world: World,
                                                  db: AsyncSession, deps: FakeDeps,
                                                  clock: FixedClock) -> None:
    old = await a_call(db, deps, world, clock, days_ago=10)
    new = await a_call(db, deps, world, clock, days_ago=2)
    old_key = old.recording_key

    kept = await api.post(pp(world, "/call-recordings/purge"), json={"days": None})
    assert kept.status_code == 200 and kept.json() == {"deleted": 0}   # 90 days by default

    purged = await api.post(pp(world, "/call-recordings/purge"), json={"days": 7})
    assert purged.status_code == 200 and purged.json() == {"deleted": 1}
    assert old_key not in deps.storage.files
    await db.refresh(old)
    await db.refresh(new)
    assert old.recording_key is None and new.recording_key is not None

    everything = await api.post(pp(world, "/call-recordings/purge"), json={"days": 0})
    assert everything.json() == {"deleted": 1} and deps.storage.files == {}
    assert (await api.post(pp(world, "/call-recordings/purge"),
                           json={"days": -1})).status_code == 400
    assert (await api.post("/api/personas/999999/call-recordings/purge",
                           json={"days": 1})).status_code == 404


async def test_the_recording_settings_of_a_persona(api: ApiClient, world: World,
                                                   db: AsyncSession) -> None:
    body = (await api.get(pp(world))).json()
    assert (body["call_recording"], body["call_recording_days"]) == (True, 90)
    saved = await api.put(pp(world), json={"call_recording": False, "call_recording_days": 30})
    assert saved.status_code == 200, saved.text
    assert (saved.json()["call_recording"], saved.json()["call_recording_days"]) == (False, 30)
    persona = await db.get(Persona, world.persona_id)
    assert persona is not None and persona.call_recording_days == 30
    for days in (0, 3651):
        assert (await api.put(pp(world), json={"call_recording_days": days})).status_code == 400
