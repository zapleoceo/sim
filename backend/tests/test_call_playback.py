"""Her live speech on a call: streamed at once, reviewed after each reply. Synthetic PCM and
fake reviews only; no live voice/provider connections."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest

from sim.adapters.db.models import Dialog, Persona
from sim.adapters.db.session import transaction
from sim.adapters.pcm import PcmBuffer
from sim.domain.enums import Delivery
from sim.ports.live_voice import AUDIO, INTERRUPTED, TRANSCRIPT, LiveVoiceEvent
from sim.services.call_playback import CallContextChanged, CallPlayback
from sim.services.call_trace import CallTrace
from sim.services.guard import BLOCKED, GuardOutcome
from tests.conftest import guard_json
from tests.test_calls import (
    FakeCall,
    FakeLive,
    FakeSession,
    chatting_world,
    dialog_messages,
    service,
    speech_events,
)

NOW = datetime(2026, 12, 31, 22, 59, 55)
PCM = b"\x02\x00" * 240          # one 480-byte frame at 24 kHz
FRAME = len(PCM)


def output():
    return PcmBuffer(FRAME), CallTrace(False, 1)


def ok(history, draft):
    return GuardOutcome("ok", draft)


async def allow(history, draft):
    return ok(history, draft)


async def test_her_speech_reaches_the_call_before_the_review_finishes():
    entered, finish = asyncio.Event(), asyncio.Event()

    async def slow_review(history, draft):
        entered.set()
        await finish.wait()
        return ok(history, draft)

    playback = CallPlayback(slow_review, clock=lambda: NOW)
    out, trace = output()
    task = asyncio.create_task(playback.run(FakeSession(speech_events(PCM), end_after_events=True),
                                            out, 24000, trace))
    await entered.wait()
    assert out.pop_frame() == PCM                      # audible while the guard still reviews
    finish.set()
    await task
    await playback.settle()
    turn = playback.turns[-1]
    assert (turn.role, turn.text, turn.released, turn.guard.verdict) == \
        ("assistant", "Да, привет! Слушаю.", True, "ok")
    assert any("проверка" in s and "разрешена" in s for s in trace.steps)
    assert not playback.failure


async def test_delivery_is_what_the_transport_accepted_across_replies():
    playback = CallPlayback(allow, clock=lambda: NOW)
    out, trace = output()
    events = [*speech_events(PCM + PCM[:100], text="Первая"), *speech_events(PCM, text="Вторая")]
    await playback.run(FakeSession(events, end_after_events=True), out, 24000, trace)
    await playback.settle()
    first, second = [t for t in playback.turns if t.role == "assistant"]
    assert first.queued_bytes == 2 * FRAME            # the partial frame is padded with silence
    assert len(out) == 3 * FRAME
    playback.acknowledge(2 * FRAME + 10)
    assert first.delivered and not second.delivered and second.played_bytes == 10
    playback.acknowledge(FRAME)
    assert second.delivered


@pytest.mark.parametrize("verdict", [GuardOutcome(BLOCKED, "x", ["Неверный день"]),
                                     GuardOutcome("rewritten", "Исправлено", ["Причина"])])
async def test_a_guard_remark_is_noted_and_the_call_goes_on(verdict):
    async def review(history, draft):
        return GuardOutcome(verdict.verdict, verdict.text, verdict.violations)

    playback = CallPlayback(review, clock=lambda: NOW)
    out, trace = output()
    await playback.run(FakeSession(speech_events(PCM), end_after_events=True), out, 24000, trace)
    await playback.settle()
    assert not playback.failure and out.pop_frame() == PCM      # the guard advises, never stops her
    assert playback.turns[-1].guard.verdict == verdict.verdict
    assert any("замечание охранника" in s and "звонок продолжается" in s for s in trace.steps)


@pytest.mark.parametrize("mode", ["error", "timeout", "context"])
async def test_a_review_that_fails_technically_keeps_the_call_going(mode):
    async def review(history, draft):
        if mode == "timeout":
            await asyncio.Event().wait()
        if mode == "context":
            raise CallContextChanged()
        raise RuntimeError("offline fake error")

    playback = CallPlayback(review, clock=lambda: NOW, timeout=0.01)
    out, trace = output()
    await playback.run(FakeSession(speech_events(PCM), end_after_events=True), out, 24000, trace)
    await playback.settle()
    assert not playback.failure and out.pop_frame() == PCM
    assert playback.turns[-1].guard is None
    assert any("звонок продолжается" in s or "проверка пропущена" in s for s in trace.steps)


async def test_the_caller_talking_over_her_cuts_the_reply_without_a_problem():
    playback = CallPlayback(allow, clock=lambda: NOW)
    out, trace = output()
    events = [LiveVoiceEvent(TRANSCRIPT, role="assistant", text="Длинный рассказ"),
              LiveVoiceEvent(AUDIO, pcm=PCM * 5), LiveVoiceEvent(INTERRUPTED),
              *speech_events(PCM, text="Да?")]
    await playback.run(FakeSession(events, end_after_events=True), out, 24000, trace)
    await playback.settle()
    cut, answer = [t for t in playback.turns if t.role == "assistant"]
    assert cut.text == "Длинный рассказ" and cut.cancel_reason and not cut.delivered
    assert len(out) == FRAME and answer.text == "Да?"
    playback.acknowledge(FRAME)
    assert answer.delivered and not playback.failure


async def test_history_of_a_review_is_the_call_so_far():
    seen = []

    async def review(history, draft):
        seen.append([(t.role, t.text) for t in history])
        return ok(history, draft)

    playback = CallPlayback(review, clock=lambda: NOW)
    out, trace = output()
    await playback.run(FakeSession(speech_events(PCM), end_after_events=True), out, 24000, trace)
    await playback.settle()
    assert seen == [[("user", "Алло, это Марина?")]]


async def test_real_call_path_streams_her_reply_and_records_the_verdict(container, world, fake_llm):
    did = await chatting_world(container, world)
    session = FakeSession(speech_events(b"\x02\x00" * 2400), end_after_events=True)   # 100 ms
    call = FakeCall(frames=1000, gap=0.05)
    await service(container, FakeLive(session)).handle(world.channel.id, call)
    assert len(call.played) == 10 and call.hung_up
    answer = (await dialog_messages(container, did))[-1]
    assert (answer.delivery, answer.guard_verdict) == (Delivery.SENT, "ok")


async def test_real_call_path_keeps_talking_after_a_guard_remark(container, world, fake_llm):
    did = await chatting_world(container, world)
    fake_llm.guard_replies = [guard_json("block", ["Неверный день"], "")]
    session = FakeSession(speech_events(b"\x02\x00" * 2400), end_after_events=True)
    call = FakeCall(frames=1000, gap=0.05)
    await asyncio.wait_for(service(container, FakeLive(session)).handle(world.channel.id, call), 10)
    assert len(call.played) == 10 and call.hung_up
    rows = await dialog_messages(container, did)
    assert (rows[-1].delivery, rows[-1].guard_verdict) == (Delivery.SENT, BLOCKED)
    assert rows[-1].guard_violations == ["Неверный день"]
    assert not next(row for row in rows if row.media_kind == "call").error
    assert not any(row.handoff_reason for row in rows)
    async with container.factory() as s:
        d = await s.get(Dialog, did)
        assert not d.ai_paused and not d.needs_operator


async def test_review_uses_current_code_clock_and_live_context(container, world, fake_llm):
    did = await chatting_world(container, world)
    container.clock = lambda: datetime(2027, 1, 1, 0, 30)
    async with transaction(container.factory) as s:
        p = await s.get(Persona, world.persona.id)
        p.timezone = "America/New_York"
    svc = service(container, FakeLive())
    from sim.services.prompt import Turn

    outcome = await svc.review_turn(did, [Turn("user", "Какой сегодня день?")], "Синтетический ответ", "voice-model")
    assert outcome.allowed
    payload = json.loads(fake_llm.guard_calls[-1][0][1]["content"])
    assert payload["trusted_facts"]["now_utc"] == "2027-01-01T00:30:00Z"
    assert "31 декабря 2026" in payload["trusted_facts"]["persona_facts"]
    assert "America/New_York" in payload["trusted_facts"]["persona_facts"]
    assert payload["dialog"][-1]["text"] == "Какой сегодня день?"
    assert payload["context"]["medium"] == "voice"
    assert fake_llm.guard_calls[-1][1]["workflow"] == "call_guard"


async def test_pause_during_voice_review_blocks_release(container, world, fake_llm):
    did = await chatting_world(container, world)
    original = fake_llm.chat

    async def pause(messages, **kw):
        async with transaction(container.factory) as s:
            (await s.get(Dialog, did)).ai_paused = True
        return await original(messages, **kw)

    fake_llm.chat = pause
    outcome = await service(container, FakeLive()).review_turn(did, [], "Ответ", "voice-model")
    assert not outcome.allowed


async def test_empty_connector_record_during_review_does_not_drop_audio(container, world, fake_llm):
    from tests.conftest import inbound
    from tests.test_ingest import ingest

    did = await chatting_world(container, world)
    original = fake_llm.chat

    async def delayed(messages, **kw):
        await ingest(container, world.channel.id, inbound("", ext="call-service"))
        return await original(messages, **kw)

    fake_llm.chat = delayed
    outcome = await service(container, FakeLive()).review_turn(did, [], "Hello", "voice-model")
    assert outcome.allowed


async def test_real_update_during_review_cancels_candidate_without_pause(container, world, fake_llm):
    did = await chatting_world(container, world)
    original = fake_llm.chat

    async def update(messages, **kw):
        async with transaction(container.factory) as s:
            (await s.get(Dialog, did)).activity_revision += 1
        return await original(messages, **kw)

    fake_llm.chat = update
    svc = service(container, FakeLive())
    with pytest.raises(CallContextChanged):
        await svc.review_turn(did, [], "Old answer", "voice-model")
    fake_llm.chat = original
    assert (await svc.review_turn(did, [], "Fresh answer", "voice-model")).allowed
    async with container.factory() as s:
        d = await s.get(Dialog, did)
        assert not d.ai_paused and not d.needs_operator
