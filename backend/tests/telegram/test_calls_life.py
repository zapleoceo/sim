"""Calls and her life (docs/persona-life.md, «Занятость»): asleep or running she does not pick
up (a missed call), busy she may decline; either way the `call` event says why and a text reply
is queued for when she is free."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.db.models import Call, ChatEvent
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


async def living_world(db: AsyncSession) -> World:
    world = await seed_world(db)
    world.persona.life = SEED["life"]
    world.persona.timezone = TZ
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


async def test_asleep_she_lets_it_ring_and_writes_later(db, sessions, clock):
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
    assert tasks.replies == [world.chat.id] and tasks.memory == []


async def test_a_caller_who_gives_up_is_not_declined(db, sessions, clock):
    world = await living_world(db)
    clock.at = utc(SUNDAY, "02:00")
    calls, tasks, sleep = service(sessions, clock)
    call = FakeIncomingCall(ended=True)
    assert await calls.handle(world.channel.id, call) == CallStatus.MISSED
    assert call.log == [] and sleep.calls == [] and tasks.replies == [world.chat.id]


async def test_busy_she_may_decline(db, sessions, clock):
    world = await living_world(db)
    clock.at = utc(SATURDAY, "20:10")                   # talking to her daughter
    calls, tasks, _ = service(sessions, clock, dice=0.1)
    call = FakeIncomingCall()
    assert await calls.handle(world.channel.id, call) == CallStatus.DECLINED
    assert call.log == ["decline"] and tasks.replies == [world.chat.id]
    event = await last_event(db, world)
    assert event.data["life_busy"] == "partial"


async def test_busy_but_she_picks_up(db, sessions, clock):
    world = await living_world(db)
    clock.at = utc(SATURDAY, "20:10")
    live = FakeLive(FakeLiveSession([], end=True))
    calls, tasks, _ = service(sessions, clock, dice=0.9, live=live)
    status = await calls.handle(world.channel.id, FakeIncomingCall())
    assert status == CallStatus.ENDED and live.opened and tasks.replies == []
