"""Synthetic PCM and fake reviews only; no live voice/provider connections."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest

from sim.adapters.db.models import Dialog, Persona
from sim.adapters.db.session import transaction
from sim.adapters.pcm import PcmBuffer
from sim.domain.enums import Delivery
from sim.ports.live_voice import AUDIO, INTERRUPTED, TRANSCRIPT, TURN_COMPLETE, LiveVoiceEvent
from sim.services.call_playback import MAX_TURN_SECONDS, CallPlayback
from sim.services.call_trace import CallTrace
from sim.services.guard import BLOCKED, GuardOutcome
from tests.conftest import guard_json
from tests.test_calls import FakeCall, FakeLive, FakeSession, chatting_world, dialog_messages, service, speech_events

NOW = datetime(2026, 12, 31, 22, 59, 55)
PCM = b"\x02\x00" * 240


def output():
    return PcmBuffer(480), CallTrace(False, 1)


async def test_no_byte_released_before_review_completes():
    entered, finish = asyncio.Event(), asyncio.Event()

    async def review(history, draft):
        entered.set()
        await finish.wait()
        return GuardOutcome("ok", draft)

    playback = CallPlayback(review, clock=lambda: NOW)
    out, trace = output()
    session = FakeSession(speech_events(PCM), end_after_events=True)
    task = asyncio.create_task(playback.run(session, out, 24000, trace))
    await entered.wait()
    assert len(out) == 0
    finish.set()
    await task
    assert out.pop_frame() == PCM
    assert playback.turns[-1].released and not playback.failure


@pytest.mark.parametrize("mode", ["block", "rewrite", "error", "timeout", "clock_boundary"])
async def test_rejected_or_stale_audio_never_released(mode):
    clock = [NOW]

    async def review(history, draft):
        if mode == "timeout":
            await asyncio.Event().wait()
        if mode == "error":
            raise RuntimeError("offline fake error")
        if mode == "clock_boundary":
            clock[0] += timedelta(seconds=10)
            return GuardOutcome("ok", draft)
        return GuardOutcome("rewritten" if mode == "rewrite" else BLOCKED,
                            "Исправлено" if mode == "rewrite" else draft, ["Причина"])

    playback = CallPlayback(review, clock=lambda: clock[0], timeout=0.01)
    out, trace = output()
    await playback.run(FakeSession(speech_events(PCM), end_after_events=True), out, 24000, trace)
    assert len(out) == 0 and playback.failure
    assert not playback.turns[-1].released


@pytest.mark.parametrize("mode", ["no_transcript", "unfinished", "oversize"])
async def test_incomplete_unbounded_audio_fails_closed(mode):
    async def forbidden_review(history, draft):
        raise AssertionError("should not review invalid audio")

    events = [LiveVoiceEvent(AUDIO, pcm=PCM)]
    if mode == "no_transcript":
        events.append(LiveVoiceEvent(TURN_COMPLETE))
    if mode == "oversize":
        events = [LiveVoiceEvent(AUDIO, pcm=b"\0" * (24000 * 2 * MAX_TURN_SECONDS + 2))]
    out, trace = output()
    playback = CallPlayback(forbidden_review, clock=lambda: NOW)
    await playback.run(FakeSession(events, end_after_events=True), out, 24000, trace)
    assert len(out) == 0 and playback.failure


async def test_interrupt_during_review_discards_old_audio_without_waiting():
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def review(history, draft):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    class Session(FakeSession):
        async def events(self):
            for event in speech_events(PCM):
                yield event
            await entered.wait()
            yield LiveVoiceEvent(INTERRUPTED)

    out, trace = output()
    playback = CallPlayback(review, clock=lambda: NOW)
    await asyncio.wait_for(playback.run(Session(), out, 24000, trace), timeout=1)
    assert cancelled.is_set() and len(out) == 0
    assert not playback.turns[-1].released


async def test_real_call_path_blocks_audio_and_marks_handoff(container, world, fake_llm):
    did = await chatting_world(container, world)
    fake_llm.guard_replies = [guard_json("block", ["Неверный день"])]
    session = FakeSession(speech_events(PCM), end_after_events=True)
    call = FakeCall(frames=1000, gap=0.05)
    await service(container, FakeLive(session)).handle(world.channel.id, call)
    assert call.played == [] and call.hung_up
    rows = await dialog_messages(container, did)
    assert rows[-1].delivery is Delivery.CANCELLED and rows[-1].guard_verdict == BLOCKED
    assert rows[-2].handoff_reason and "Неверный день" in rows[-2].handoff_reason
    async with container.factory() as s:
        d = await s.get(Dialog, did)
        assert d.ai_paused and d.needs_operator


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
