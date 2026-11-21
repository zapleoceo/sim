"""Calls and her life (docs/attention.md, «Звонки»): asleep or running she does not pick up (a
missed call), busy she may decline; either way the `call` event says why and what she was
doing, and her phone check is asked for — she texts back when she can look. The attention
settings change the picture: «Всегда сразу» always answers, without «не беспокоить» her
sleep is only busy, and without «Писать после пропущенного звонка» no check is asked for."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.db.models import Call, ChatEvent, Persona
from sim.domain.enums import CallStatus, EventKind
from sim.telegram.calls.service import MISSED_RING_S, CallService
from tests.fakes.telegram import (
    FakeChatResolver,
    FakeContextBuilder,
    FakeIncomingCall,
    FakeLive,
    FakeLiveSession,
    FakeTasks,
    RecordingSleep,
    TestSessions,
)
from tests.services.life_seed import SATURDAY, SEED, TZ, utc
from tests.telegram.conftest import World, seed_world
from tests.telegram.test_calls import settings

SUNDAY = date(2026, 11, 22)
ASLEEP_LEFT_S = 4.5 * 3600              # 02:00 -> the end of sleep at 06:30
GLANCE_MAX_S = 120.0


async def living_world(db: AsyncSession, **attention: Any) -> World:
    world = await seed_world(db)
    world.persona.life = SEED["life"]
    world.persona.timezone = TZ
    world.persona.attention = attention
    await db.commit()
    return world


def service(sessions: TestSessions, clock: FixedClock, *, dice: float = 0.9,
            live: FakeLive | None = None) -> tuple[CallService, FakeTasks, RecordingSleep]:
    tasks, sleep = FakeTasks(), RecordingSleep()

    def uniform(low: float, high: float) -> float:
        return dice if (low, high) == (0.0, 1.0) else (low + high) / 2

    return CallService(sessions=sessions, settings=settings(), clock=clock,
                       live=live or FakeLive(), inbox=FakeChatResolver(),
                       context=FakeContextBuilder(), tasks=tasks, sleep=sleep,
                       uniform=uniform), tasks, sleep


async def last_event(db: AsyncSession, world: World) -> ChatEvent:
    return (await db.scalars(select(ChatEvent).where(
        ChatEvent.chat_id == world.chat.id, ChatEvent.kind == EventKind.CALL)
        .order_by(ChatEvent.id.desc()))).first()  # type: ignore[return-value]


async def test_asleep_she_lets_it_ring_and_looks_after_waking(db, sessions, clock):
    world = await living_world(db)
    clock.at = utc(SUNDAY, "02:00")
    live = FakeLive()
    calls, tasks, sleep = service(sessions, clock, live=live)
    call = FakeIncomingCall()

    assert await calls.handle(world.channel.id, call) == CallStatus.MISSED

    assert live.opened == [] and call.log == ["decline"]
    assert sum(sleep.calls) == MISSED_RING_S            # rang unanswered, then gave up
    [row] = (await db.scalars(select(Call))).all()
    assert row.status == CallStatus.MISSED and (row.error or "").startswith("away: сон")
    event = await last_event(db, world)
    assert event.data["life_busy"] == "away" and event.data["status"] == "missed"
    assert event.data["life_activity"] == "сон, телефон в другой комнате"
    [(persona_id, delay)] = tasks.checks
    assert persona_id == world.persona.id
    assert ASLEEP_LEFT_S < delay <= ASLEEP_LEFT_S + GLANCE_MAX_S
    persona = await db.get(Persona, world.persona.id)
    assert persona is not None
    await db.refresh(persona)
    assert persona.attention_check_at is not None and tasks.memory == []


async def test_a_caller_who_gives_up_is_not_declined(db, sessions, clock):
    world = await living_world(db)
    clock.at = utc(SUNDAY, "02:00")
    calls, tasks, sleep = service(sessions, clock)
    call = FakeIncomingCall(ended=True)
    assert await calls.handle(world.channel.id, call) == CallStatus.MISSED
    assert call.log == [] and sleep.calls == [] and len(tasks.checks) == 1


async def test_busy_she_may_decline(db, sessions, clock):
    world = await living_world(db)
    clock.at = utc(SATURDAY, "20:10")                   # talking to her daughter until 20:30
    calls, tasks, _ = service(sessions, clock, dice=0.1)
    call = FakeIncomingCall()
    assert await calls.handle(world.channel.id, call) == CallStatus.DECLINED
    assert call.log == ["decline"]
    event = await last_event(db, world)
    assert event.data["life_busy"] == "partial"
    [(_, delay)] = tasks.checks
    assert 180 <= delay <= 20 * 60                       # busy: a look within the call


async def test_busy_but_she_picks_up(db, sessions, clock):
    world = await living_world(db)
    clock.at = utc(SATURDAY, "20:10")
    live = FakeLive(FakeLiveSession([], end=True))
    calls, tasks, _ = service(sessions, clock, dice=0.9, live=live)
    status = await calls.handle(world.channel.id, FakeIncomingCall())
    assert status == CallStatus.ENDED and live.opened and tasks.checks == []


async def test_always_at_once_answers_even_at_night(db, sessions, clock):
    world = await living_world(db, mode="instant")
    clock.at = utc(SUNDAY, "02:00")
    live = FakeLive(FakeLiveSession([], end=True))
    calls, tasks, _ = service(sessions, clock, live=live)
    assert await calls.handle(world.channel.id, FakeIncomingCall()) == CallStatus.ENDED
    assert live.opened and tasks.checks == []


async def test_the_chat_switch_answers_at_once_too(db, sessions, clock):
    world = await living_world(db)
    world.chat.attention_instant = True
    await db.commit()
    clock.at = utc(SUNDAY, "02:00")
    live = FakeLive(FakeLiveSession([], end=True))
    calls, _, _ = service(sessions, clock, live=live)
    assert await calls.handle(world.channel.id, FakeIncomingCall()) == CallStatus.ENDED


async def test_without_do_not_disturb_her_sleep_is_only_busy(db, sessions, clock):
    world = await living_world(db, night_dnd=False)
    clock.at = utc(SUNDAY, "02:00")
    calls, tasks, _ = service(sessions, clock, dice=0.1)
    assert await calls.handle(world.channel.id, FakeIncomingCall()) == CallStatus.DECLINED
    event = await last_event(db, world)
    assert event.data["life_busy"] == "partial"
    [(_, delay)] = tasks.checks
    assert delay <= 20 * 60                              # a glance at night, not the morning


async def test_no_text_after_a_missed_call_when_the_owner_turned_it_off(db, sessions, clock):
    world = await living_world(db, after_missed_call=False)
    clock.at = utc(SUNDAY, "02:00")
    calls, tasks, _ = service(sessions, clock)
    call = FakeIncomingCall(ended=True)
    assert await calls.handle(world.channel.id, call) == CallStatus.MISSED
    assert tasks.checks == []
