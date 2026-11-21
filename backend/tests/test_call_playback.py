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
from sim.services.call_playback import (
    MAX_TURN_SECONDS,
    REVIEW_FAILED,
    CallContextChanged,
    CallPlayback,
    is_hello,
)
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
            clock[0] += timedelta(minutes=1)
            return GuardOutcome("ok", draft)
        return GuardOutcome("rewritten" if mode == "rewrite" else BLOCKED,
                            "Исправлено" if mode == "rewrite" else draft, ["Причина"])

    playback = CallPlayback(review, clock=lambda: clock[0], timeout=0.01)
    out, trace = output()
    await playback.run(FakeSession(speech_events(PCM), end_after_events=True), out, 24000, trace)
    assert len(out) == 0
    assert not playback.turns[-1].released
    if mode in ("error", "timeout"):
        # A technical failure drops this reply only; the call goes on.
        assert not playback.failure and playback.turns[-1].cancel_reason == REVIEW_FAILED
        assert any("реплика не прозвучала, звонок продолжается" in s for s in trace.steps)
    else:
        assert playback.failure
        assert any("Звонок завершается" in s for s in trace.steps)


def two_replies(first: str, second: str) -> list[LiveVoiceEvent]:
    return [LiveVoiceEvent(TRANSCRIPT, role="assistant", text=first),
            LiveVoiceEvent(AUDIO, pcm=PCM), LiveVoiceEvent(TURN_COMPLETE),
            LiveVoiceEvent(TRANSCRIPT, role="assistant", text=second),
            LiveVoiceEvent(AUDIO, pcm=PCM), LiveVoiceEvent(TURN_COMPLETE)]


async def test_repeated_review_failures_end_the_call():
    async def review(history, draft):
        raise RuntimeError("offline fake error")

    playback = CallPlayback(review, clock=lambda: NOW)
    out, trace = output()
    session = FakeSession(two_replies("Да, слушаю.", "Расскажу подробнее."), end_after_events=True)
    await playback.run(session, out, 24000, trace)
    assert len(out) == 0 and playback.failure.startswith("Проверка ответа не завершилась 2 раза")
    assert all(not t.released for t in playback.turns)


@pytest.mark.parametrize("hello", ["Алло?", "алло", " АЛЛО! "])
async def test_the_first_hello_plays_without_a_review(hello):
    reviewed: list[str] = []

    async def review(history, draft):
        reviewed.append(draft)
        return GuardOutcome("ok", draft)

    playback = CallPlayback(review, clock=lambda: NOW)
    out, trace = output()
    events = [LiveVoiceEvent(TRANSCRIPT, role="assistant", text=hello),
              LiveVoiceEvent(AUDIO, pcm=PCM), LiveVoiceEvent(TURN_COMPLETE)]
    await playback.run(FakeSession(events, end_after_events=True), out, 24000, trace)
    assert reviewed == [] and out.pop_frame() == PCM
    assert playback.turns[0].released and playback.turns[0].guard.allowed
    assert any("фиксированное приветствие, звучит без проверки" in s for s in trace.steps)


async def test_a_hello_after_her_first_reply_is_reviewed_like_any_reply():
    reviewed: list[str] = []

    async def review(history, draft):
        reviewed.append(draft)
        if draft == "Здравствуйте":
            raise RuntimeError("offline fake error")     # dropped unheard, the call goes on
        return GuardOutcome("ok", draft)

    playback = CallPlayback(review, clock=lambda: NOW)
    out, trace = output()
    await playback.run(FakeSession(two_replies("Здравствуйте", "Алло"), end_after_events=True),
                       out, 24000, trace)
    assert reviewed == ["Здравствуйте", "Алло"] and not playback.failure
    assert out.pop_frame() == PCM and playback.turns[-1].released


def test_only_the_bare_hello_counts_as_the_greeting():
    assert is_hello("Алло?") and is_hello("  алло… ")
    assert not is_hello("Алло, это Марина") and not is_hello("Здравствуйте")


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


@pytest.mark.parametrize("tail", [b"", b"\x02\x00"])
async def test_next_turn_review_waits_for_playback_and_includes_approved_history(tail):
    first_checked = asyncio.Event()
    clock = [NOW]
    seen = []

    async def review(history, draft):
        seen.append((clock[0], list(history)))
        first_checked.set()
        return GuardOutcome("ok", draft)

    out, trace = output()
    playback = CallPlayback(review, clock=lambda: clock[0])
    session = FakeSession([*speech_events(PCM + tail), *speech_events(PCM)], end_after_events=True)
    task = asyncio.create_task(playback.run(session, out, 24000, trace))
    await first_checked.wait()
    assert len(seen) == 1
    clock[0] += timedelta(seconds=10)
    first_turn = playback.playing
    assert first_turn is not None
    frame = out.pop_frame()
    assert frame == PCM
    playback.acknowledge(first_turn, len(frame))
    if tail:
        assert not first_turn.delivered
        frame = out.pop_frame()
        assert frame == tail.ljust(len(PCM), b"\x00")
        playback.acknowledge(first_turn, len(frame))
    assert first_turn.delivered and len(out) == 0
    await asyncio.wait_for(task, timeout=1)
    assert len(seen) == 2 and seen[1][0] == clock[0]
    assert any(t.role == "assistant" for t in seen[1][1])
    assert out.pop_frame() == PCM and not playback.failure


@pytest.mark.parametrize("stale", [False, True])
async def test_minute_boundary_rechecks_neutral_or_stale_reply(stale):
    clock = [NOW]
    seen = []

    async def review(history, draft):
        seen.append(clock[0])
        if len(seen) == 1:
            clock[0] += timedelta(seconds=10)
        elif stale:
            return GuardOutcome(BLOCKED, draft, ["Stale clock assertion"])
        return GuardOutcome("ok", draft)

    playback = CallPlayback(review, clock=lambda: clock[0])
    out, trace = output()
    await playback.run(FakeSession(speech_events(PCM), end_after_events=True), out, 24000, trace)
    assert seen == [NOW, NOW + timedelta(seconds=10)]
    assert bool(len(out)) is not stale
    assert bool(playback.failure) is stale


@pytest.mark.parametrize("played_frames", [0, 1])
async def test_interruption_after_release_never_marks_incomplete_turn_sent(container, world, played_frames):
    did = await chatting_world(container, world)
    approved = asyncio.Event()

    async def review(history, draft):
        approved.set()
        return GuardOutcome("ok", draft)

    out, trace = output()
    playback = CallPlayback(review, clock=lambda: NOW)

    class Session(FakeSession):
        async def events(self):
            for event in speech_events(PCM * 2):
                yield event
            await approved.wait()
            assert playback.turns[-1].released
            for _ in range(played_frames):
                frame = out.pop_frame()
                playback.acknowledge(playback.playing, len(frame))
            yield LiveVoiceEvent(INTERRUPTED)

    session = Session()
    await playback.run(session, out, 24000, trace)
    turn = playback.turns[-1]
    assert not turn.delivered and turn.played_bytes == played_frames * len(PCM)
    assert len(out) == 0 and playback.drained.is_set()
    await service(container, FakeLive()).record(did, await session.finish(), NOW, turns=playback.turns)
    row = (await dialog_messages(container, did))[-1]
    assert row.delivery is Delivery.CANCELLED
    assert f"{played_frames * len(PCM)}/{2 * len(PCM)}" in row.error


@pytest.mark.parametrize("verdict", ["block", "rewrite", "ok"])
async def test_real_call_path_blocks_audio_without_handoff(container, world, fake_llm, verdict):
    did = await chatting_world(container, world)
    fake_llm.guard_replies = [guard_json(verdict, ["Неверный день"], "Guard replacement")]
    session = FakeSession(speech_events(PCM), end_after_events=True)
    call = FakeCall(frames=1000, gap=0.05)
    await service(container, FakeLive(session)).handle(world.channel.id, call)
    assert call.played == [] and call.hung_up
    rows = await dialog_messages(container, did)
    assert rows[-1].delivery is Delivery.CANCELLED and rows[-1].guard_verdict == BLOCKED
    assert not any(row.handoff_reason for row in rows)
    assert "Неверный день" in next(row for row in rows if row.media_kind == "call").error
    async with container.factory() as s:
        d = await s.get(Dialog, did)
        assert not d.ai_paused and not d.needs_operator
    assert (await service(container, FakeLive()).review_turn(did, [], "Hello", "voice-model")).allowed


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


async def test_stale_candidate_does_not_close_next_turn_or_mark_handoff(container, world):
    did = await chatting_world(container, world)
    checked = asyncio.Event()
    calls = []

    async def review(history, draft):
        calls.append(draft)
        if len(calls) == 1:
            checked.set()
            raise CallContextChanged()
        assert all(t.text != "Old answer" for t in history)
        return GuardOutcome("ok", draft)

    class Session(FakeSession):
        async def events(self):
            for event in speech_events(PCM, text="Old answer"):
                yield event
            await checked.wait()
            for event in speech_events(PCM, text="Fresh answer"):
                yield event

    out, trace = output()
    playback = CallPlayback(review, clock=lambda: NOW)
    session = Session()
    await playback.run(session, out, 24000, trace)
    assert not playback.failure and not next(t for t in playback.turns if t.role == "assistant").released
    assert playback.turns[-1].released and len(out) == len(PCM)
    await service(container, FakeLive()).record(did, await session.finish(), NOW,
        turns=playback.turns, failure=playback.failure)
    async with container.factory() as s:
        d = await s.get(Dialog, did)
        assert not d.ai_paused and not d.needs_operator
