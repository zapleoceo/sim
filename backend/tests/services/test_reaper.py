"""Rows a failure left half-done after their commit are picked up by the reaper
(sim/services/reaper.py, docs/deploy.md «Застрявшие строки»)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.db.models import Chat, ChatEvent, Generation, Message, ReplyRun
from sim.domain.enums import (
    Author,
    Direction,
    EventKind,
    GenerationKind,
    GenerationStatus,
    MessageKind,
    MessageStatus,
    RunStatus,
)
from sim.services.deps import Deps
from sim.services.generation_state import GENERATION_FRESH, active_generations, run_budget
from sim.services.reaper import REPLY_RUN_BUDGET, Reaper
from tests.services.conftest import Fakes
from tests.services.factories import all_rows, fresh, make_chat

MINUTE = timedelta(minutes=1)


async def outgoing(db: AsyncSession, chat: Chat, ago: timedelta, clock: FixedClock, *,
                   status: MessageStatus = MessageStatus.PENDING) -> Message:
    message = Message(chat_id=chat.id, direction=Direction.OUT, author=Author.PERSONA,
                      kind=MessageKind.TEXT, text="привет", status=status,
                      created_at=clock.now() - ago)
    db.add(message)
    await db.flush()
    return message


async def generation(db: AsyncSession, chat: Chat, kind: GenerationKind,
                     status: GenerationStatus, changed: timedelta,
                     clock: FixedClock) -> Generation:
    """`changed`: how long ago it was queued (or claimed, for a running one)."""
    row = Generation(persona_id=chat.persona_id, chat_id=chat.id, kind=kind, prompt="селфи",
                     caption="у окна", status=status, created_at=clock.now() - changed,
                     updated_at=clock.now() - changed)
    db.add(row)
    await db.flush()
    return row


async def test_a_message_left_pending_is_queued_again(db: AsyncSession, deps: Deps,
                                                      fakes: Fakes, clock: FixedClock) -> None:
    chat = await make_chat(db)
    stuck = await outgoing(db, chat, 10 * MINUTE, clock)
    await outgoing(db, chat, MINUTE, clock)                          # its send is on the way
    await outgoing(db, chat, 10 * MINUTE, clock, status=MessageStatus.SENT)
    await outgoing(db, chat, timedelta(days=2), clock)               # not sent out of the blue

    reaped = await Reaper(deps).run()

    assert reaped.resent == [stuck.id]
    assert fakes.tasks.of("send") == [("send", stuck.id, chat.id)]
    assert (await fresh(db, stuck)).status == MessageStatus.PENDING   # the send decides


async def test_generations_are_queued_again_or_failed_by_their_budget(
        db: AsyncSession, deps: Deps, fakes: Fakes, clock: FixedClock) -> None:
    chat = await make_chat(db)
    G, S = GenerationKind, GenerationStatus
    lost = await generation(db, chat, G.PHOTO, S.QUEUED, 10 * MINUTE, clock)
    just_queued = await generation(db, chat, G.PHOTO, S.QUEUED, MINUTE, clock)
    never_started = await generation(db, chat, G.PHOTO, S.QUEUED, GENERATION_FRESH + MINUTE,
                                     clock)
    photo_running = await generation(db, chat, G.PHOTO, S.RUNNING, 15 * MINUTE, clock)
    photo_dead = await generation(db, chat, G.PHOTO, S.RUNNING,
                                  run_budget(G.PHOTO) + MINUTE, clock)
    # The same age is fine for a video: the photo first, then the clip.
    video_running = await generation(db, chat, G.VIDEO, S.RUNNING,
                                     run_budget(G.PHOTO) + MINUTE, clock)
    done = await generation(db, chat, G.PHOTO, S.DONE, timedelta(hours=3), clock)

    reaped = await Reaper(deps).run()

    assert reaped.requeued == [lost.id]
    assert fakes.tasks.of("generate") == [("generate", lost.id)]
    assert reaped.failed_generations == [never_started.id, photo_dead.id]
    assert [(await fresh(db, row)).status for row in (
        lost, just_queued, never_started, photo_running, photo_dead, video_running, done)] == [
        S.QUEUED, S.QUEUED, S.FAILED, S.RUNNING, S.FAILED, S.RUNNING, S.DONE]
    assert photo_dead.error == "не закончилась за 20 мин: воркер остановился"
    assert never_started.error == "не началась за 40 мин"
    # Like a generation error: the operator sees it, the chat's status stops (once per chat).
    events = await all_rows(db, select(ChatEvent).where(
        ChatEvent.chat_id == chat.id, ChatEvent.kind == EventKind.GENERATION_FAILED)
        .order_by(ChatEvent.id))
    assert [event.generation_id for event in events] == [never_started.id, photo_dead.id]
    assert events[0].data["caption"] == "у окна"
    assert fakes.tasks.actions() == ["cancel"]
    # One definition of «in progress»: every row still in progress is shown and promised.
    active = await active_generations(db, [chat.id], clock.now())
    assert [row.id for row in active[chat.id]] == [
        lost.id, just_queued.id, photo_running.id, video_running.id]


async def test_a_turn_whose_worker_died_is_failed(db: AsyncSession, deps: Deps,
                                                  clock: FixedClock) -> None:
    chat = await make_chat(db)
    dead = ReplyRun(chat_id=chat.id, status=RunStatus.RUNNING,
                    created_at=clock.now() - REPLY_RUN_BUDGET - MINUTE)
    thinking = ReplyRun(chat_id=chat.id, status=RunStatus.RUNNING,
                        created_at=clock.now() - 5 * MINUTE)
    sent = ReplyRun(chat_id=chat.id, status=RunStatus.SENT,
                    created_at=clock.now() - timedelta(hours=2))
    db.add_all([dead, thinking, sent])
    await db.flush()

    reaped = await Reaper(deps).run()

    assert reaped.failed_runs == [dead.id]
    dead = await fresh(db, dead)
    assert (dead.status, dead.error, dead.finished_at) == (
        RunStatus.FAILED, "ход оборвался: не закончился за 30 мин", clock.now())
    assert (await fresh(db, thinking)).status == RunStatus.RUNNING
    assert (await fresh(db, sent)).status == RunStatus.SENT


async def test_nothing_stuck_does_nothing(db: AsyncSession, deps: Deps, fakes: Fakes) -> None:
    await make_chat(db)
    reaped = await Reaper(deps).run()
    assert reaped.empty
    assert fakes.tasks.calls == []
