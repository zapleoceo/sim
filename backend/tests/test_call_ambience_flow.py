"""Contextual-call lifecycle and cadence, with fake PCM/providers and no local processes."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from sim.adapters.audio import AudioError, run_process
from sim.adapters.call_ambience import CallAmbience
from sim.adapters.pcm import PcmBuffer, frame_bytes
from sim.ports.live_voice import AUDIO, INTERRUPTED, LiveVoiceEvent
from sim.services.call_trace import CallTrace
from sim.services.calls import MAX_LAG_S, CallService
from tests.test_call_ambience import RATE, noise
from tests.test_calls import FakeCall, FakeLive, FakeSession, service


async def test_interruption_drops_speech_but_continues_ambient_frames(container):
    session = FakeSession([
        LiveVoiceEvent(AUDIO, pcm=b"\x10\x00" * 24000),  # a second of unplayed speech
        LiveVoiceEvent(INTERRUPTED),
    ])
    call = FakeCall(frames=4, gap=0.02)
    call.rate_hz = RATE
    trace = CallTrace(False, 1)
    await asyncio.wait_for(service(container, FakeLive(session)).converse(
        call, session, trace, CallAmbience(noise(), RATE)), 2)
    assert trace.counters["interruptions"] == 1
    assert trace.counters["frames_out"] == 0
    assert trace.counters["ambience_frames_out"] > 0
    assert "first_frame_sent" not in trace.seen
    assert call.played and any(any(frame) for frame in call.played)
    assert all(len(frame) == frame_bytes(RATE) for frame in call.played)


async def test_finite_downlink_drains_speech_not_the_endless_ambient_loop(container):
    session = FakeSession([LiveVoiceEvent(AUDIO, pcm=b"\x10\x00" * 2400)],
                          end_after_events=True)  # 100 ms at the model's 24 kHz
    call = FakeCall(frames=1000, gap=0.02)          # caller deliberately remains connected
    call.rate_hz = RATE
    trace = CallTrace(False, 1)
    await asyncio.wait_for(service(container, FakeLive(session)).converse(
        call, session, trace, CallAmbience(noise(), RATE)), 2)
    assert trace.counters["frames_out"] == 10
    assert trace.counters["ambience_frames_out"] <= 2
    assert 10 <= len(call.played) <= 12
    assert "first_frame_sent" in trace.seen
    # converse cancels the endless pacer when speech is drained; nothing plays afterwards.
    finished = len(call.played)
    await asyncio.sleep(0)
    assert len(call.played) == finished


class PacedEnough(Exception):
    """Deterministically finish the fake-clock pacer after a fixed number of frames."""


@pytest.mark.parametrize("overshoot, stall_after", [(0.004, None), (0.0, 5)])
async def test_ambient_pacer_preserves_realtime_cadence_across_speech_and_pauses(
        overshoot, stall_after):
    now = [0.0]
    sent = []
    out = PcmBuffer(frame_bytes(RATE))
    out.push(b"\x10\x00" * (RATE // 100) * 3)  # speech first, then background only
    trace = CallTrace(False, 1)

    async def play(data):
        assert len(data) == frame_bytes(RATE)
        sent.append(now[0])
        if len(sent) == stall_after:
            now[0] += 1.0

    async def sleep(seconds):
        if len(sent) == 50:
            raise PacedEnough
        now[0] += seconds + overshoot

    svc = CallService(None, None, None, sleep=sleep, monotonic=lambda: now[0])
    with pytest.raises(PacedEnough):
        await svc.pace(SimpleNamespace(play=play), out, trace, CallAmbience(noise(), RATE))
    assert len(sent) == 50
    assert trace.counters["frames_out"] == 3
    assert trace.counters["ambience_frames_out"] == 47
    if stall_after is None:
        assert sent[-1] == pytest.approx(49 * 0.010 + overshoot)
        assert trace.counters["pacer_restarts"] == 0
    else:
        gaps = [b - a for a, b in zip(sent, sent[1:], strict=False)]
        assert gaps[4] > MAX_LAG_S
        assert trace.counters["pacer_restarts"] == 1
        assert all(gap == pytest.approx(0.010) for gap in gaps[5:])


@pytest.mark.parametrize("already_exited", [False, True])
async def test_timeout_drains_pipes_even_when_process_exits_before_kill(monkeypatch, already_exited):
    actions = []

    async def communicate(data=None):
        if data is not None:
            raise TimeoutError
        actions.append("drain")
        return b"last bytes", b"last error"

    def kill():
        actions.append("kill")
        raise ProcessLookupError  # exited between returncode inspection and kill

    process = SimpleNamespace(returncode=0 if already_exited else None,
                              communicate=communicate, kill=kill)

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(AudioError, match="timed out"):
        await run_process(("ffmpeg",), b"", 1)
    assert actions == (["drain"] if already_exited else ["kill", "drain"])


async def test_second_cancellation_does_not_cancel_pipe_reaper(monkeypatch):
    initial_started = asyncio.Event()
    drain_started = asyncio.Event()
    may_finish = asyncio.Event()
    drained = asyncio.Event()
    actions = []

    async def communicate(data=None):
        if data is not None:
            initial_started.set()
            await asyncio.Event().wait()
        drain_started.set()
        await may_finish.wait()
        drained.set()
        return b"", b""

    process = SimpleNamespace(returncode=None, communicate=communicate,
                              kill=lambda: actions.append("kill"))

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(run_process(("ffmpeg",), b"", 5))
    try:
        await asyncio.wait_for(initial_started.wait(), 1)
        task.cancel()
        await asyncio.wait_for(drain_started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert actions == ["kill"]
        may_finish.set()
        await asyncio.wait_for(drained.wait(), 1)
    finally:
        may_finish.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
