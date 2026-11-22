"""Her own call (docs/initiative.md): `CallService.dial` of a started planned contact — the
checks, the `call` row (direction out), ringing, the talk, and the contact closed with the
outcome; nobody picked up -> she texts later. Also the speed of her speech in a call and the
`telegram:place_call` task. Fake call backend and live session; the test's database."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Call, ChatEvent, PlannedContact
from sim.domain.enums import (
    CallDirection,
    CallStatus,
    ContactKind,
    ContactSource,
    ContactStatus,
    EventKind,
)
from sim.domain.initiative import NO_ANSWER_TOPIC, RING_OUT_S
from sim.ports import LiveEvent, Peer, PortError
from sim.services.deps import reset_deps, set_deps
from sim.telegram import tasks as telegram_tasks
from sim.telegram.calls.service import CallService
from tests.fakes.ai import FakeMediaTools
from tests.fakes.telegram import (
    FakeCallBackend,
    FakeCallContext,
    FakeChatResolver,
    FakeContextBuilder,
    FakeIncomingCall,
    FakeLive,
    FakeLiveSession,
    FakeOutgoingCall,
    FakeStorage,
    FakeTasks,
    FakeTempoFactory,
    RecordingSleep,
)
from tests.telegram.conftest import World, seed_world
from tests.telegram.test_calls import (
    SILENCE_20MS,
    call_events,
    call_rows,
    make_service,
    settings,
)

TOPIC = "договорились созвониться"
DUE = datetime(2026, 11, 21, 18, 30, tzinfo=UTC)


async def contact_of(db: AsyncSession, world: World, *,
                     source: ContactSource = ContactSource.MANAGER,
                     status: ContactStatus = ContactStatus.STARTED) -> PlannedContact:
    contact = PlannedContact(chat_id=world.chat.id, kind=ContactKind.CALL, source=source,
                             status=status, topic=TOPIC, due_at=DUE, created_by="operator:1",
                             result="")
    db.add(contact)
    await db.flush()
    await db.commit()
    return contact


async def fresh_contact(db: AsyncSession, contact: PlannedContact) -> PlannedContact:
    await db.refresh(contact)
    return contact


async def initiative_events(db: AsyncSession, world: World) -> list[ChatEvent]:
    return list((await db.scalars(select(ChatEvent).where(
        ChatEvent.chat_id == world.chat.id, ChatEvent.kind == EventKind.INITIATIVE)
        .order_by(ChatEvent.id))).all())


async def test_her_call_rings_talks_and_closes_the_contact(db, sessions, clock):
    world = await seed_world(db)
    contact = await contact_of(db, world)
    session = FakeLiveSession([LiveEvent("transcript_in", text="Алло?"),
                               LiveEvent("transcript_out", text="Привет, это я!")])
    call = FakeOutgoingCall(frames=[SILENCE_20MS] * 2, hang_up=session.emitted)
    backend = FakeCallBackend(call)
    live = FakeLive(session)
    service, tasks, sleep, context = make_service(sessions, clock, live)

    status = await service.dial(contact.id, {world.channel.id: backend})

    assert status == CallStatus.ENDED
    assert backend.dialed == [Peer(external_id="1001", access_hash="555")]
    assert call.rings == [RING_OUT_S] and call.log == ["ring", "hangup"]
    assert sleep.calls == []                            # she does not wait: she is calling
    assert context.topics == [TOPIC] and context.chats == [world.chat.id]
    assert len(live.opened) == 1 and session.finished.is_set()
    [row] = await call_rows(db)
    assert (row.status, row.direction, row.external_id) == (CallStatus.ENDED, CallDirection.OUT,
                                                            "1001")
    assert [(line["who"], line["text"]) for line in row.transcript] == [
        ("client", "Алло?"), ("persona", "Привет, это я!")]
    [event] = await call_events(db, world)
    assert event.actor == "persona" and event.call_id == row.id
    assert event.data["direction"] == "out" and event.data["after_call"] is True
    contact = await fresh_contact(db, contact)
    assert (contact.status, contact.call_id) == (ContactStatus.DONE, row.id)
    assert contact.result.startswith("поговорили") and contact.finished_at == clock.now()
    assert await initiative_events(db, world) == []
    # After the talk she does what she promised there.
    assert tasks.replies == [(world.chat.id, 30.0)] and tasks.memory == [world.chat.id]


@pytest.mark.parametrize(("reason", "status", "result"), [
    ("no_answer", CallStatus.MISSED, "не взял трубку"),
    ("declined", CallStatus.DECLINED, "сбросил звонок"),
    ("busy", CallStatus.DECLINED, "занято"),
])
async def test_nobody_picked_up_she_texts_why_she_called(db, sessions, clock, reason, status,
                                                         result):
    world = await seed_world(db)
    contact = await contact_of(db, world)
    live = FakeLive()
    call = FakeOutgoingCall(no_answer=reason)
    service, tasks, _, _ = make_service(sessions, clock, live)

    assert await service.dial(contact.id, {world.channel.id: FakeCallBackend(call)}) == status

    assert live.opened == [] and call.log == ["ring"]
    [row] = await call_rows(db)
    assert (row.status, row.direction) == (status, CallDirection.OUT)
    [event] = await call_events(db, world)
    assert event.actor == "persona" and event.data["end_reason"] == reason
    contact = await fresh_contact(db, contact)
    assert (contact.status, contact.result) == (ContactStatus.FAILED, result)
    [initiative] = await initiative_events(db, world)
    assert initiative.actor == "persona" and initiative.in_context
    assert initiative.data == {"contact_id": contact.id, "kind": "message",
                               "topic": NO_ANSWER_TOPIC.format(topic=TOPIC),
                               "source": "persona"}
    # Within minutes (the midpoint of NO_ANSWER_TEXT_S here); no memory refresh, no talk.
    assert tasks.replies == [(world.chat.id, 150.0)] and tasks.memory == []


async def test_her_own_idea_needs_the_chat_switch(db, sessions, clock):
    world = await seed_world(db)
    contact = await contact_of(db, world, source=ContactSource.PERSONA)
    backend = FakeCallBackend(FakeOutgoingCall(no_answer="no_answer"))
    service, tasks, _, context = make_service(sessions, clock, FakeLive())

    assert await service.dial(contact.id, {world.channel.id: backend}) is None

    assert backend.dialed == [] and context.topics == [] and await call_rows(db) == []
    contact = await fresh_contact(db, contact)
    assert contact.status == ContactStatus.FAILED
    assert contact.result == "не позвонила: «Звонит сама» выключили"
    assert contact.finished_at == clock.now() and tasks.replies == []

    world.chat.calls_out = True
    await db.commit()
    again = await contact_of(db, world, source=ContactSource.PERSONA)
    assert await service.dial(again.id, {world.channel.id: backend}) == CallStatus.MISSED
    assert len(backend.dialed) == 1


@pytest.mark.parametrize(("seed", "config", "reason"), [
    ({}, {"calls_enabled": False}, "звонки выключены на сервере"),
    ({"persona_calls": False}, {}, "звонки выключены у персоны"),
    ({"live_model": ""}, {}, "не выбрана модель звонка"),
    ({"chat_ai": False}, {}, "AI is off in this chat"),
    ({"needs_operator": True}, {}, "the chat waits for an operator"),
])
async def test_a_call_she_must_not_make_fails_the_contact(db, sessions, clock, seed, config,
                                                          reason):
    world = await seed_world(db, **seed)
    contact = await contact_of(db, world)
    backend = FakeCallBackend()
    service, _, _, _ = make_service(sessions, clock, FakeLive(), config=settings(**config))

    assert await service.dial(contact.id, {world.channel.id: backend}) is None

    assert backend.dialed == [] and await call_rows(db) == []
    contact = await fresh_contact(db, contact)
    assert (contact.status, contact.result) == (ContactStatus.FAILED, f"не позвонила: {reason}")


async def test_no_call_while_another_one_goes_on(db, sessions, clock):
    world = await seed_world(db)
    db.add(Call(chat_id=world.chat.id, direction=CallDirection.IN, status=CallStatus.ACTIVE))
    await db.flush()
    contact = await contact_of(db, world)
    service, _, _, _ = make_service(sessions, clock, FakeLive())

    assert await service.dial(contact.id, {world.channel.id: FakeCallBackend()}) is None

    contact = await fresh_contact(db, contact)
    assert contact.result == "не позвонила: с ним уже идёт звонок"


@pytest.mark.parametrize("status", [ContactStatus.PLANNED, ContactStatus.CANCELLED,
                                    ContactStatus.DONE])
async def test_only_a_started_contact_is_dialled(db, sessions, clock, status):
    world = await seed_world(db)
    contact = await contact_of(db, world, status=status)
    backend = FakeCallBackend()
    service, _, _, _ = make_service(sessions, clock, FakeLive())

    assert await service.dial(contact.id, {world.channel.id: backend}) is None
    assert await service.dial(999_999, {world.channel.id: backend}) is None

    assert backend.dialed == [] and await call_rows(db) == []
    assert (await fresh_contact(db, contact)).status == status


async def test_without_calls_on_the_account_the_call_fails(db, sessions, clock):
    world = await seed_world(db)
    contact = await contact_of(db, world)
    service, tasks, _, _ = make_service(sessions, clock, FakeLive())

    assert await service.dial(contact.id, {}) == CallStatus.FAILED

    [row] = await call_rows(db)
    assert (row.status, row.error) == (CallStatus.FAILED,
                                       "calls of this account are not connected")
    contact = await fresh_contact(db, contact)
    assert contact.status == ContactStatus.FAILED
    assert contact.result == "звонок сорвался: calls of this account are not connected"
    assert tasks.replies == [] and await initiative_events(db, world) == []


async def test_a_ring_error_hangs_up_and_fails(db, sessions, clock):
    world = await seed_world(db)
    contact = await contact_of(db, world)
    call = FakeOutgoingCall(ring_error=RuntimeError("ntgcalls"))
    service, _, _, _ = make_service(sessions, clock, FakeLive())

    status = await service.dial(contact.id, {world.channel.id: FakeCallBackend(call)})

    assert status == CallStatus.FAILED and call.log == ["ring", "hangup"]
    assert (await call_rows(db))[0].error == "dial: RuntimeError: ntgcalls"
    assert (await fresh_contact(db, contact)).status == ContactStatus.FAILED


async def test_a_backend_that_can_not_dial_fails_the_call(db, sessions, clock):
    world = await seed_world(db)
    contact = await contact_of(db, world)
    backend = FakeCallBackend(error=RuntimeError("no entity"))
    service, _, _, _ = make_service(sessions, clock, FakeLive())

    assert await service.dial(contact.id, {world.channel.id: backend}) == CallStatus.FAILED
    assert (await call_rows(db))[0].error == "dial: RuntimeError: no entity"


async def test_a_broker_refusal_after_the_pick_up_hangs_up(db, sessions, clock):
    world = await seed_world(db)
    contact = await contact_of(db, world)
    call = FakeOutgoingCall()
    service, _, _, _ = make_service(sessions, clock, FakeLive(error=PortError("no paid key")))

    status = await service.dial(contact.id, {world.channel.id: FakeCallBackend(call)})

    assert status == CallStatus.FAILED and call.log == ["ring", "hangup"]
    assert (await call_rows(db))[0].error == "live: PortError: no paid key"
    contact = await fresh_contact(db, contact)
    assert contact.result == "звонок сорвался: live: PortError: no paid key"


async def test_a_context_failure_is_recorded_and_not_dialled(db, sessions, clock):
    world = await seed_world(db)
    contact = await contact_of(db, world)
    backend = FakeCallBackend()
    service, _, _, context = make_service(
        sessions, clock, FakeLive(), context=FakeContextBuilder(error=RuntimeError("no prompt")))

    assert await service.dial(contact.id, {world.channel.id: backend}) is None

    assert backend.dialed == [] and context.topics == [TOPIC]
    [row] = await call_rows(db)
    assert row.status == CallStatus.FAILED and "no prompt" in (row.error or "")
    contact = await fresh_contact(db, contact)
    assert (contact.status, contact.result) == (ContactStatus.FAILED, "не позвонила: контекст")


async def test_her_speed_goes_through_the_tempo_of_the_call(db, sessions, clock):
    world = await seed_world(db)
    session = FakeLiveSession([LiveEvent("audio", audio=b"\x01\x00" * 480),
                               LiveEvent("turn_complete")])
    call = FakeIncomingCall(hang_up=session.emitted)
    tempo = FakeTempoFactory()
    service = CallService(
        sessions=sessions, settings=settings(), clock=clock, live=FakeLive(session),
        inbox=FakeChatResolver(), context=FakeContextBuilder(context=FakeCallContext(speed=1.3)),
        tasks=FakeTasks(), sleep=RecordingSleep(), uniform=lambda low, high: (low + high) / 2,
        tempo=tempo)

    assert await service.handle(world.channel.id, call) == CallStatus.ENDED

    [made] = tempo.made
    assert made.args == (1.3, 24_000, 48_000)
    assert made.log[:2] == ["feed", "flush"] and made.log[-1] == "reset"


# --- the recording of a call (docs/calls.md, «Запись звонка») ----------------------------------


def recording_service(sessions, clock, session: FakeLiveSession, *, record: bool = True,
                      tools: FakeMediaTools | None = None,
                      storage: FakeStorage | None = None) -> CallService:
    return CallService(
        sessions=sessions, settings=settings(), clock=clock, live=FakeLive(session),
        inbox=FakeChatResolver(),
        context=FakeContextBuilder(context=FakeCallContext(record=record)),
        tasks=FakeTasks(), sleep=RecordingSleep(), uniform=lambda low, high: (low + high) / 2,
        tools=tools, storage=storage)


def talking() -> tuple[FakeLiveSession, FakeIncomingCall]:
    session = FakeLiveSession([LiveEvent("audio", audio=b"\x01\x00" * 480),
                               LiveEvent("transcript_out", text="Привет!")])
    return session, FakeIncomingCall(frames=[SILENCE_20MS] * 3, hang_up=session.emitted)


async def test_a_recorded_call_is_stored_as_stereo_ogg(db, sessions, clock):
    world = await seed_world(db)
    session, call = talking()
    tools, storage = FakeMediaTools(), FakeStorage()
    service = recording_service(sessions, clock, session, tools=tools, storage=storage)

    assert await service.handle(world.channel.id, call) == CallStatus.ENDED

    [row] = await call_rows(db)
    await db.refresh(row)
    [(name, size, rate)] = [c for c in tools.calls if c[0] == "call_recording"]
    assert (name, rate) == ("call_recording", 16_000) and size > 0 and size % 4 == 0
    assert row.recording_key == "calls/0"
    assert storage.files["calls/0"].startswith(b"OGG")
    assert row.recording_s == round(size / (4 * 16_000), 2)


async def test_a_failed_encoding_keeps_the_call_without_audio(db, sessions, clock):
    world = await seed_world(db)
    session, call = talking()
    tools, storage = FakeMediaTools(recording_error=PortError("ffmpeg exited 1")), FakeStorage()
    service = recording_service(sessions, clock, session, tools=tools, storage=storage)

    assert await service.handle(world.channel.id, call) == CallStatus.ENDED

    [row] = await call_rows(db)
    assert row.status == CallStatus.ENDED and row.recording_key is None
    assert storage.files == {}


@pytest.mark.parametrize("parts", ["record off", "no tools"])
async def test_no_recording_when_it_is_off_or_can_not_be_made(db, sessions, clock, parts):
    world = await seed_world(db)
    session, call = talking()
    tools, storage = FakeMediaTools(), FakeStorage()
    if parts == "record off":
        service = recording_service(sessions, clock, session, record=False, tools=tools,
                                    storage=storage)
    else:
        service = recording_service(sessions, clock, session, storage=storage)

    assert await service.handle(world.channel.id, call) == CallStatus.ENDED

    assert tools.calls == [] and storage.files == {}
    assert (await call_rows(db))[0].recording_key is None


async def test_a_call_where_nothing_was_heard_is_not_recorded(db, sessions, clock):
    world = await seed_world(db)
    session = FakeLiveSession([LiveEvent("transcript_in", text="алло")])
    call = FakeIncomingCall(hang_up=session.emitted)     # no audio either way
    tools, storage = FakeMediaTools(), FakeStorage()
    service = recording_service(sessions, clock, session, tools=tools, storage=storage)

    assert await service.handle(world.channel.id, call) == CallStatus.ENDED
    assert tools.calls == [] and storage.files == {}


# --- telegram:place_call -----------------------------------------------------------------------


class RecordingDialer:
    def __init__(self) -> None:
        self.dialled: list[int] = []

    async def dial(self, contact_id: int) -> None:
        self.dialled.append(contact_id)


async def place_call(contact_id: int) -> Any:
    """The task's own function (a procrastinate Task keeps it as `func`)."""
    return await telegram_tasks.place_call.func(contact_id)


async def test_place_call_goes_to_the_dialer_of_the_process():
    dialer = RecordingDialer()
    telegram_tasks.set_dialer(dialer)
    try:
        await place_call(7)
    finally:
        telegram_tasks.set_dialer(None)
    assert dialer.dialled == [7]


async def test_without_calls_in_the_process_the_contact_fails(db, sessions, clock):
    world = await seed_world(db)
    contact = await contact_of(db, world)
    telegram_tasks.set_dialer(None)
    set_deps(SimpleNamespace(sessions=sessions, clock=clock))  # type: ignore[arg-type]
    try:
        await place_call(contact.id)
    finally:
        reset_deps()
    contact = await fresh_contact(db, contact)
    assert (contact.status, contact.result) == (ContactStatus.FAILED, telegram_tasks.NO_CALLS)
    assert contact.finished_at == clock.now()
