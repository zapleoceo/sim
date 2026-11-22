"""Planned contacts against the database (sim/services/initiative.py, docs/initiative.md):
planning by the manager and by her, cancelling, the minute tick that starts the due ones (a
message -> an `initiative` event and a reply turn; a call -> started and dialled), lost calls,
whether she may call herself now, and that a due message waits for her answer like a client
message does."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.db.models import Call, Chat, ChatEvent, Persona, PlannedContact
from sim.domain.enums import (
    CallDirection,
    CallStatus,
    ChannelKind,
    ContactKind,
    ContactSource,
    ContactStatus,
    Direction,
    EventKind,
)
from sim.domain.initiative import MAX_TOPIC, STALE_START
from sim.services.deps import Deps, reset_deps, set_deps
from sim.services.initiative import (
    InitiativeError,
    Started,
    calls_unavailable,
    cancel_contact,
    chat_contacts,
    contact_view,
    her_call_block,
    plan_contact,
    plan_her_call,
    queue_started,
    start_due,
    write_first,
)
from sim.services.turn_state import answered_until, unanswered, waiting_counts
from sim.tasks import agent
from tests.services.conftest import Fakes
from tests.services.factories import (
    add_event,
    add_message,
    all_rows,
    fresh,
    make_chat,
    make_persona,
)

MANAGER = "operator:1"


async def plan(db: AsyncSession, chat: Chat, clock: FixedClock, *,
               kind: ContactKind = ContactKind.MESSAGE, topic: str = "спросить про отпуск",
               due_at: datetime | None = None,
               source: ContactSource = ContactSource.MANAGER,
               calls_enabled: bool = True) -> PlannedContact:
    return await plan_contact(
        db, chat, kind=kind, topic=topic, due_at=due_at or clock.now() + timedelta(hours=1),
        source=source, created_by=MANAGER if source == ContactSource.MANAGER else "persona",
        now=clock.now(), calls_enabled=calls_enabled)


async def contact(db: AsyncSession, chat: Chat, *, kind: ContactKind, due_at: datetime,
                  status: ContactStatus = ContactStatus.PLANNED,
                  source: ContactSource = ContactSource.MANAGER,
                  call_id: int | None = None) -> PlannedContact:
    row = PlannedContact(chat_id=chat.id, kind=kind, source=source, status=status,
                         topic="спросить про отпуск", due_at=due_at, created_by=MANAGER,
                         result="", call_id=call_id)
    db.add(row)
    await db.flush()
    return row


async def initiative_events(db: AsyncSession, chat: Chat) -> list[ChatEvent]:
    return await all_rows(db, select(ChatEvent).where(
        ChatEvent.chat_id == chat.id, ChatEvent.kind == EventKind.INITIATIVE)
        .order_by(ChatEvent.id))


# --- planning ----------------------------------------------------------------------------------


async def test_the_manager_plans_a_message(db: AsyncSession, clock: FixedClock) -> None:
    chat = await make_chat(db)
    due = clock.now() + timedelta(hours=2)
    row = await plan(db, chat, clock, topic="  спросить   про\nотпуск ", due_at=due)
    assert (row.kind, row.source, row.status) == (ContactKind.MESSAGE, ContactSource.MANAGER,
                                                  ContactStatus.PLANNED)
    assert (row.topic, row.due_at, row.created_by, row.result) == (
        "спросить про отпуск", due, MANAGER, "")
    view = contact_view(row)
    assert view["kind"] == "message" and view["status"] == "planned"
    assert view["source"] == "manager" and view["due_at"] == due.isoformat()
    assert view["finished_at"] is None and view["call_id"] is None
    assert [c.id for c in await chat_contacts(db, chat.id)] == [row.id]


async def test_the_managers_time_must_not_be_past(db: AsyncSession, clock: FixedClock) -> None:
    chat = await make_chat(db)
    with pytest.raises(InitiativeError, match="время уже прошло"):
        await plan(db, chat, clock, due_at=clock.now() - timedelta(minutes=5))
    # «Now» typed in the form a few seconds late is fine: it is due at once.
    row = await plan(db, chat, clock, due_at=clock.now() - timedelta(seconds=30))
    assert row.due_at == clock.now()


@pytest.mark.parametrize(("topic", "error"), [
    ("   ", "Напиши тему"),
    ("а" * (MAX_TOPIC + 1), f"длиннее {MAX_TOPIC}"),
])
async def test_a_topic_is_required_and_short(db: AsyncSession, clock: FixedClock, topic: str,
                                             error: str) -> None:
    chat = await make_chat(db)
    with pytest.raises(InitiativeError, match=error):
        await plan(db, chat, clock, topic=topic)


async def test_a_call_needs_working_calls(db: AsyncSession, clock: FixedClock) -> None:
    chat = await make_chat(db)
    row = await plan(db, chat, clock, kind=ContactKind.CALL)
    assert row.kind == ContactKind.CALL           # the manager needs no «Звонит сама»
    with pytest.raises(InitiativeError, match="Звонки выключены на сервере"):
        await plan(db, chat, clock, kind=ContactKind.CALL, calls_enabled=False)
    no_calls = await make_chat(db, await make_persona(db, name="Без звонков", calls_enabled=False))
    with pytest.raises(InitiativeError, match="Звонки выключены у персоны"):
        await plan(db, no_calls, clock, kind=ContactKind.CALL)
    no_model = await make_chat(db, await make_persona(db, name="Без модели", models={"live": ""}))
    with pytest.raises(InitiativeError, match="Не выбрана модель звонка"):
        await plan(db, no_model, clock, kind=ContactKind.CALL)
    # A message needs none of it.
    assert (await plan(db, no_model, clock)).kind == ContactKind.MESSAGE


async def test_calls_work_only_in_telegram(db: AsyncSession) -> None:
    persona = await make_persona(db)
    assert calls_unavailable(persona, ChannelKind.TELEGRAM_USER, calls_enabled=True) == ""
    for kind in (ChannelKind.INSTAGRAM, ChannelKind.WHATSAPP):
        assert calls_unavailable(persona, kind,
                                 calls_enabled=True) == "звонки есть только в Telegram"


async def test_her_own_call_dials_within_a_minute(db: AsyncSession, clock: FixedClock) -> None:
    chat = await make_chat(db)
    row = await plan_her_call(db, chat.id, " договорились  созвониться ", clock.now(),
                              uniform=lambda low, high: low)
    assert (row.kind, row.source, row.created_by) == (ContactKind.CALL, ContactSource.PERSONA,
                                                      "persona")
    assert row.topic == "договорились созвониться"
    assert row.due_at == clock.now() + timedelta(seconds=20)
    with pytest.raises(InitiativeError):
        await plan_her_call(db, 999_999, "созвониться", clock.now())


async def test_only_a_planned_contact_is_cancelled(db: AsyncSession, clock: FixedClock) -> None:
    chat = await make_chat(db)
    row = await plan(db, chat, clock)
    cancel_contact(row, clock.now())
    assert (row.status, row.finished_at) == (ContactStatus.CANCELLED, clock.now())
    with pytest.raises(InitiativeError, match="ещё не началось"):
        cancel_contact(row, clock.now())


# --- the minute tick -----------------------------------------------------------------------------


async def test_a_due_message_becomes_her_initiative(db: AsyncSession,
                                                    clock: FixedClock) -> None:
    chat = await make_chat(db)
    row = await contact(db, chat, kind=ContactKind.MESSAGE, due_at=clock.now())
    later = await contact(db, chat, kind=ContactKind.MESSAGE,
                          due_at=clock.now() + timedelta(minutes=1))

    started = await start_due(db, clock.now())

    assert started == Started(replies=(chat.id,), calls=())
    [event] = await initiative_events(db, chat)
    assert event.actor == MANAGER and event.in_context
    assert event.data == {"contact_id": row.id, "kind": "message",
                          "topic": "спросить про отпуск", "source": "manager"}
    row = await fresh(db, row)
    assert (row.status, row.result, row.event_id) == (ContactStatus.DONE,
                                                      "она написала первой", event.id)
    assert row.finished_at == clock.now()
    assert (await fresh(db, later)).status == ContactStatus.PLANNED
    # Started once: the next tick finds nothing.
    assert await start_due(db, clock.now()) == Started()


async def test_a_due_call_is_started_for_the_telegram_process(db: AsyncSession,
                                                              clock: FixedClock) -> None:
    chat = await make_chat(db)
    row = await contact(db, chat, kind=ContactKind.CALL,
                        due_at=clock.now() - timedelta(minutes=1))
    assert await start_due(db, clock.now()) == Started(calls=(row.id,))
    assert (await fresh(db, row)).status == ContactStatus.STARTED
    assert await initiative_events(db, chat) == []


async def test_a_started_call_whose_job_was_lost_fails(db: AsyncSession,
                                                       clock: FixedClock) -> None:
    chat = await make_chat(db)
    old = clock.now() - STALE_START - timedelta(minutes=1)
    lost = await contact(db, chat, kind=ContactKind.CALL, due_at=old,
                         status=ContactStatus.STARTED)
    recent = await contact(db, chat, kind=ContactKind.CALL,
                           due_at=clock.now() - timedelta(minutes=2),
                           status=ContactStatus.STARTED)
    ringing = Call(chat_id=chat.id, direction=CallDirection.OUT, status=CallStatus.ACTIVE)
    ended = Call(chat_id=chat.id, direction=CallDirection.OUT, status=CallStatus.FAILED)
    db.add_all([ringing, ended])
    await db.flush()
    talking = await contact(db, chat, kind=ContactKind.CALL, due_at=old,
                            status=ContactStatus.STARTED, call_id=ringing.id)
    cut_off = await contact(db, chat, kind=ContactKind.CALL, due_at=old,
                            status=ContactStatus.STARTED, call_id=ended.id)

    assert await start_due(db, clock.now()) == Started()

    for row in (lost, cut_off):
        row = await fresh(db, row)
        assert (row.status, row.result) == (ContactStatus.FAILED,
                                            "звонок прервался или не начался")
    for row in (recent, talking):
        assert (await fresh(db, row)).status == ContactStatus.STARTED


async def test_started_contacts_are_queued_after_the_commit(fakes: Fakes) -> None:
    await queue_started(fakes.tasks, Started(replies=(5, 6), calls=(7,)))
    assert fakes.tasks.calls == [("reply", 5, 0), ("reply", 6, 0), ("place_call", 7)]


async def test_the_minute_tick_starts_and_queues(db: AsyncSession, deps: Deps, fakes: Fakes,
                                                 clock: FixedClock) -> None:
    chat = await make_chat(db)
    message = await contact(db, chat, kind=ContactKind.MESSAGE, due_at=clock.now())
    call = await contact(db, chat, kind=ContactKind.CALL, due_at=clock.now())
    set_deps(deps)
    try:
        await agent.contacts_tick(0)
    finally:
        reset_deps()
    assert fakes.tasks.calls == [("reply", chat.id, 0), ("place_call", call.id)]
    assert (await fresh(db, message)).status == ContactStatus.DONE
    assert (await fresh(db, call)).status == ContactStatus.STARTED


# --- it waits for her answer ---------------------------------------------------------------------


async def test_a_due_message_waits_for_her_answer(db: AsyncSession, clock: FixedClock) -> None:
    chat = await make_chat(db)
    await add_message(db, chat, "привет", at=clock.now() - timedelta(hours=2))
    await add_message(db, chat, "привет!", at=clock.now() - timedelta(hours=2),
                      direction=Direction.OUT)
    assert await unanswered(db, chat.id) is None

    event_id = await write_first(db, chat.id, topic="спросить про отпуск",
                                 source=ContactSource.MANAGER, actor=MANAGER)

    waiting = await unanswered(db, chat.id)
    assert waiting is not None
    assert (waiting.count, waiting.last_in_event_id, waiting.last_in_message_id) == (
        1, event_id, None)
    counts = await waiting_counts(db, {chat.id: await answered_until(db, chat.id)})
    assert counts[chat.id].count == 1 and counts[chat.id].last_event_id == event_id


async def test_a_call_she_talked_on_waits_for_her_too(db: AsyncSession,
                                                      clock: FixedClock) -> None:
    chat = await make_chat(db)
    at = clock.now() - timedelta(minutes=3)
    await add_event(db, chat, EventKind.CALL, at=at, data={"status": "ended"})
    assert await unanswered(db, chat.id) is None        # a plain call asks for nothing
    talk = await add_event(db, chat, EventKind.CALL, at=at,
                           data={"status": "ended", "after_call": True})
    waiting = await unanswered(db, chat.id)
    assert waiting is not None and (waiting.count, waiting.last_in_event_id) == (1, talk.id)


# --- may she call herself now? -------------------------------------------------------------------


async def block(db: AsyncSession, chat: Chat, now: datetime, *,
                kind: ChannelKind = ChannelKind.TELEGRAM_USER) -> str:
    persona = await db.get(Persona, chat.persona_id)
    assert persona is not None
    return await her_call_block(db, chat, persona, now, channel_kind=kind, calls_enabled=True)


async def test_her_call_needs_the_chat_switch(db: AsyncSession, clock: FixedClock) -> None:
    chat = await make_chat(db)                          # 20:30 in Kyiv
    assert await block(db, chat, clock.now()) == "в этом чате звонить самой нельзя"
    chat.calls_out = True
    await db.flush()
    assert await block(db, chat, clock.now()) == ""
    assert await block(db, chat, clock.now(),
                       kind=ChannelKind.WHATSAPP) == "звонки есть только в Telegram"
    late = clock.now() + timedelta(hours=2)             # 22:30 in Kyiv
    assert await block(db, chat, late) == "сейчас слишком поздно или рано для звонка"


async def test_her_call_stays_rare(db: AsyncSession, clock: FixedClock) -> None:
    chat = await make_chat(db)
    chat.calls_out = True
    now = clock.now()
    db.add(Call(chat_id=chat.id, direction=CallDirection.OUT, status=CallStatus.MISSED,
                created_at=now - timedelta(hours=2)))
    # An incoming call does not count as hers.
    db.add(Call(chat_id=chat.id, direction=CallDirection.IN, status=CallStatus.ENDED,
                created_at=now - timedelta(minutes=10)))
    await db.flush()
    assert await block(db, chat, now) == "ты недавно уже звонила"
    assert await block(db, chat, now + timedelta(hours=1)) == ""     # 21:30 in Kyiv


async def test_a_planned_call_of_hers_is_pending(db: AsyncSession, clock: FixedClock) -> None:
    chat = await make_chat(db)
    chat.calls_out = True
    await contact(db, chat, kind=ContactKind.CALL, due_at=clock.now() + timedelta(hours=1))
    assert await block(db, chat, clock.now()) == "ты уже набираешь его"


async def test_three_calls_a_day_at_most(db: AsyncSession, clock: FixedClock) -> None:
    chat = await make_chat(db)
    chat.calls_out = True
    now = clock.now()                                   # 20:30 in Kyiv
    for hours in (4, 7, 10):                            # 16:30, 13:30, 10:30 today
        db.add(Call(chat_id=chat.id, direction=CallDirection.OUT, status=CallStatus.ENDED,
                    created_at=now - timedelta(hours=hours)))
    await db.flush()
    assert await block(db, chat, now) == "сегодня ты уже звонила достаточно"
