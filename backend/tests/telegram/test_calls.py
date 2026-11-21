"""CallService: who she answers, the live session, transcript, the Call row and its `call`
event, memory refresh. Fake call backend and fake Broker live session; the test's database."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.config import Settings
from sim.db.models import Call, ChatEvent
from sim.domain.enums import CallDirection, CallStatus, EventKind
from sim.ports import LiveEvent, PortError
from sim.telegram.calls.service import CallService, refuse_reason
from tests.fakes.telegram import (
    FakeCallContext,
    FakeChatResolver,
    FakeContextBuilder,
    FakeIncomingCall,
    FakeLive,
    FakeLiveSession,
    FakeTasks,
    RecordingSleep,
    TestSessions,
    gone,
)
from tests.telegram.conftest import World, seed_world

SILENCE_20MS = b"\x00\x00" * 960            # 48 kHz mono s16le


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"calls_enabled": True, "call_ring_min_s": 3.0,
                              "call_ring_max_s": 7.0, "call_max_seconds": 30}
    return Settings(**{**values, **overrides})


def make_service(sessions: TestSessions, clock: FixedClock, live: FakeLive, *,
                 context: FakeContextBuilder | None = None,
                 config: Settings | None = None) -> tuple[CallService, FakeTasks, RecordingSleep,
                                                          FakeContextBuilder]:
    tasks, sleep = FakeTasks(), RecordingSleep()
    context = context or FakeContextBuilder()
    service = CallService(
        sessions=sessions, settings=config or settings(), clock=clock, live=live,
        inbox=FakeChatResolver(), context=context, tasks=tasks, sleep=sleep,
        uniform=lambda low, high: (low + high) / 2)
    return service, tasks, sleep, context


async def call_rows(db: AsyncSession) -> list[Call]:
    return list((await db.scalars(select(Call).order_by(Call.id))).all())


async def call_events(db: AsyncSession, world: World) -> list[ChatEvent]:
    return list((await db.scalars(select(ChatEvent).where(
        ChatEvent.chat_id == world.chat.id, ChatEvent.kind == EventKind.CALL)
        .order_by(ChatEvent.id))).all())


async def test_an_answered_call_is_bridged_transcribed_and_recorded(db, sessions, clock):
    world = await seed_world(db)
    session = FakeLiveSession([
        LiveEvent("audio", audio=b"\x01\x00" * 480),
        LiveEvent("transcript_in", text="Привет"),
        LiveEvent("transcript_out", text="Привет, "),
        LiveEvent("transcript_out", text="как дела?"),
    ])
    live = FakeLive(session)
    call = FakeIncomingCall(frames=[SILENCE_20MS] * 3, hang_up=session.emitted)
    service, tasks, sleep, context = make_service(sessions, clock, live)

    status = await service.handle(world.channel.id, call)

    assert status == CallStatus.ENDED
    assert sleep.calls == [5.0]                         # the ring delay
    assert call.log == ["answer", "hangup"]
    assert live.opened == [{"model": "gemini-live", "system": "Ты Марина.",
                            "voice": "Vindemiatrix", "language": "ru", "greeting": "Алло?",
                            "max_seconds": 30, "key_tier": "paid"}]
    assert context.chats == [world.chat.id]
    assert session.audio_in and all(len(chunk) >= 1280 for chunk in session.audio_in)
    assert session.finished.is_set()
    [row] = await call_rows(db)
    assert (row.status, row.direction, row.external_id) == (CallStatus.ENDED, CallDirection.IN,
                                                            "1001")
    assert [(line["who"], line["text"]) for line in row.transcript] == [
        ("client", "Привет"), ("persona", "Привет, как дела?")]
    assert all(line["t"] >= 0 for line in row.transcript)
    assert row.started_at == clock.now()
    assert row.duration_s is not None and row.ended_at is not None
    assert (row.cost_usd, row.model, row.error) == (0.12, "gemini-live", None)
    [event] = await call_events(db, world)
    assert event.call_id == row.id and event.actor == "client" and event.in_context
    assert event.data["status"] == "ended" and event.data["end_reason"] == "hung_up"
    assert tasks.memory == [world.chat.id]


@pytest.mark.parametrize(("seed", "config", "reason"), [
    ({}, {"calls_enabled": False}, "calls are disabled"),
    ({"persona_calls": False}, {}, "persona calls are off"),
    ({"live_model": ""}, {}, "no live model"),
    ({"client_ai": False}, {}, "AI is off for this client"),
    ({"chat_ai": False}, {}, "AI is off in this chat"),
    # The AI gate is the same as for messages: a chat waiting for an operator is silent.
    ({"needs_operator": True}, {}, "the chat waits for an operator"),
])
async def test_calls_she_must_not_take_are_declined_and_recorded(db, sessions, clock, seed,
                                                                 config, reason):
    world = await seed_world(db, **seed)
    live = FakeLive()
    call = FakeIncomingCall()
    service, tasks, sleep, context = make_service(sessions, clock, live,
                                                  config=settings(**config))
    assert await service.handle(world.channel.id, call) == CallStatus.DECLINED
    assert call.log == ["decline"]
    assert live.opened == [] and context.chats == [] and sleep.calls == []
    [row] = await call_rows(db)
    assert row.status == CallStatus.DECLINED and reason in (row.error or "")
    assert row.ended_at == clock.now()
    [event] = await call_events(db, world)
    assert event.data["status"] == "declined" and reason in event.data["error"]
    assert tasks.memory == []


async def test_a_caller_who_hangs_up_while_ringing_is_a_missed_call(db, sessions, clock):
    world = await seed_world(db)
    live = FakeLive()
    call = FakeIncomingCall(ended=True)
    service, tasks, _, _ = make_service(sessions, clock, live)
    assert await service.handle(world.channel.id, call) == CallStatus.MISSED
    assert live.opened == [] and call.log == []
    [row] = await call_rows(db)
    assert row.status == CallStatus.MISSED
    [event] = await call_events(db, world)
    assert event.data["status"] == "missed"
    assert tasks.memory == []


async def test_a_caller_gone_during_pick_up_is_missed_and_the_session_closed(db, sessions, clock):
    world = await seed_world(db)
    live = FakeLive()
    call = FakeIncomingCall(answer_error=gone())
    service, _, _, _ = make_service(sessions, clock, live)
    assert await service.handle(world.channel.id, call) == CallStatus.MISSED
    assert live.session.finished.is_set()
    assert (await call_rows(db))[0].status == CallStatus.MISSED


async def test_a_broker_refusal_fails_the_call_before_picking_up(db, sessions, clock):
    world = await seed_world(db)
    live = FakeLive(error=PortError("no paid key"))
    call = FakeIncomingCall()
    service, _, _, _ = make_service(sessions, clock, live)
    assert await service.handle(world.channel.id, call) == CallStatus.FAILED
    assert call.log == ["decline"]
    [row] = await call_rows(db)
    assert row.status == CallStatus.FAILED and row.error == "live: PortError: no paid key"
    [event] = await call_events(db, world)
    assert event.data["status"] == "failed"


async def test_an_answer_error_fails_the_call(db, sessions, clock):
    world = await seed_world(db)
    live = FakeLive()
    call = FakeIncomingCall(answer_error=RuntimeError("ntgcalls"))
    service, _, _, _ = make_service(sessions, clock, live)
    assert await service.handle(world.channel.id, call) == CallStatus.FAILED
    assert (await call_rows(db))[0].error == "answer: RuntimeError: ntgcalls"


async def test_a_context_failure_fails_the_call(db, sessions, clock):
    world = await seed_world(db)
    live = FakeLive()
    call = FakeIncomingCall()
    builder = FakeContextBuilder(error=RuntimeError("no persona prompt"))
    service, _, _, _ = make_service(sessions, clock, live, context=builder)
    assert await service.handle(world.channel.id, call) == CallStatus.FAILED
    assert call.log == ["decline"] and live.opened == []
    assert "no persona prompt" in ((await call_rows(db))[0].error or "")


async def test_a_model_error_ends_the_call_with_the_error(db, sessions, clock):
    world = await seed_world(db)
    session = FakeLiveSession([LiveEvent("transcript_out", text="Алло?"),
                               LiveEvent("error", text="quota exceeded")], end=True)
    call = FakeIncomingCall(hang_up=asyncio.Event())       # the caller would stay on
    service, tasks, _, _ = make_service(sessions, clock, FakeLive(session))
    assert await service.handle(world.channel.id, call) == CallStatus.ENDED
    [row] = await call_rows(db)
    assert row.error == "quota exceeded"
    assert row.transcript[0]["text"] == "Алло?"
    [event] = await call_events(db, world)
    assert event.data["end_reason"] == "model_error"
    assert call.log == ["answer", "hangup"]
    assert tasks.memory == [world.chat.id]


async def test_when_the_model_finishes_the_broker_transcript_is_the_fallback(db, sessions,
                                                                            clock):
    world = await seed_world(db)
    summary = {"cost_usd": 0.05, "model": "live-x",
               "transcript": [{"role": "user", "text": "алло"},
                              {"role": "assistant", "text": " привет  "}, {"role": "user"}]}
    session = FakeLiveSession([LiveEvent("done")], end=True, summary=summary)
    call = FakeIncomingCall(hang_up=asyncio.Event())
    service, _, _, _ = make_service(sessions, clock, FakeLive(session))
    assert await service.handle(world.channel.id, call) == CallStatus.ENDED
    [row] = await call_rows(db)
    assert [(line["who"], line["text"]) for line in row.transcript] == [
        ("client", "алло"), ("persona", "привет")]
    assert (row.model, row.cost_usd, row.error) == ("live-x", 0.05, None)


async def test_calls_left_open_by_a_restart_are_closed_as_failed(db, sessions, clock):
    world = await seed_world(db)
    db.add_all([Call(chat_id=world.chat.id, direction=CallDirection.IN,
                     status=CallStatus.ACTIVE),
                Call(chat_id=world.chat.id, direction=CallDirection.IN,
                     status=CallStatus.ENDED)])
    await db.flush()
    await db.commit()
    service, _, _, _ = make_service(sessions, clock, FakeLive())
    assert await service.close_stale() == 1
    rows = await call_rows(db)
    assert [r.status for r in rows] == [CallStatus.FAILED, CallStatus.ENDED]
    [event] = await call_events(db, world)
    assert event.data["status"] == "failed"


LIVE = {"live": {"model": "m"}}         # persona.agents with the call agent's model


def test_refuse_reason_order():
    persona = SimpleNamespace(id=1, is_active=True, ai_enabled=True, calls_enabled=True,
                              agents=LIVE)
    client = SimpleNamespace(ai_enabled=True)
    chat = SimpleNamespace(ai_enabled=True, persona_id=1, needs_operator=False)
    off = "persona AI is off (or the persona is archived)"
    assert refuse_reason(calls_enabled=True, persona=persona, client=client, chat=chat) is None
    assert refuse_reason(calls_enabled=True, persona=SimpleNamespace(
        id=1, is_active=True, ai_enabled=False, calls_enabled=True, agents=LIVE),
        client=client, chat=chat) == off
    archived = SimpleNamespace(id=1, is_active=False, ai_enabled=True, calls_enabled=True,
                               agents=LIVE)
    assert refuse_reason(calls_enabled=True, persona=archived, client=client, chat=chat) == off
    other = SimpleNamespace(id=2, is_active=True, ai_enabled=True, calls_enabled=True,
                            agents=LIVE)
    assert refuse_reason(calls_enabled=True, persona=other, client=client, chat=chat) == off
    assert refuse_reason(calls_enabled=True, persona=persona, client=None,
                         chat=chat) == "AI is off for this client"
    waiting = SimpleNamespace(ai_enabled=True, persona_id=1, needs_operator=True)
    assert refuse_reason(calls_enabled=True, persona=persona, client=client,
                         chat=waiting) == "the chat waits for an operator"
    # The AI gate comes before her own call settings: a call-less persona in a chat waiting
    # for an operator is declined for the operator.
    no_calls = SimpleNamespace(id=1, is_active=True, ai_enabled=True, calls_enabled=False,
                               agents=LIVE)
    assert refuse_reason(calls_enabled=True, persona=no_calls, client=client,
                         chat=waiting) == "the chat waits for an operator"
    assert refuse_reason(calls_enabled=False, persona=persona, client=client,
                         chat=waiting) == "calls are disabled (SIM_CALLS_ENABLED)"


def test_fake_context_matches_the_shared_contract():
    fields = {"system", "greeting", "language", "voice", "model", "max_seconds", "key_tier"}
    assert set(vars(FakeCallContext())) == fields
