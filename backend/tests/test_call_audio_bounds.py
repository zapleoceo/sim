"""Synthetic call transport saturation; no Telegram or Broker network use."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from sim.adapters.channels import tg_calls
from sim.adapters.channels.tg_calls import NativePcmMailbox, TelegramCall
from sim.adapters.llm import broker_live
from sim.adapters.llm.broker_live import BrokerLiveSession, BrokerLiveVoice
from sim.ports.live_voice import AUDIO, INTERRUPTED, TRANSCRIPT, LiveVoiceError


class Stream:
    def __init__(self, events=(), ready=None):
        self.events = events
        self.ready = ready
        self.closed = False

    async def __aiter__(self):
        for event in self.events:
            yield event if isinstance(event, bytes) else json.dumps(event)

    async def recv(self):
        return json.dumps(self.ready)

    async def send(self, data):
        return None

    async def close(self):
        self.closed = True


async def test_telegram_input_stays_bounded_and_hangup_survives_saturation(monkeypatch):
    monkeypatch.setattr(tg_calls, "MAX_INPUT_FRAMES", 2)
    monkeypatch.setattr(tg_calls, "MAX_INPUT_FRAME_BYTES", 2)
    call = TelegramCall(SimpleNamespace(rate_hz=48000), 42)
    call.feed(b"12")
    call.feed(b"34")
    call.feed(b"56")
    call.feed(b"oversized")
    assert call.inbox.qsize() == 2
    call.remote_ended()
    call.remote_ended()
    call.feed(b"78")
    assert [pcm async for pcm in call.frames()] == [b"34", b"56"]


async def test_empty_telegram_call_queue_wakes_when_hung_up():
    call = TelegramCall(SimpleNamespace(rate_hz=48000), 42)
    frames = call.frames()
    waiting = asyncio.ensure_future(anext(frames, None))
    await asyncio.sleep(0)
    call.remote_ended()
    assert await asyncio.wait_for(waiting, 1) is None


async def test_stalled_loop_native_burst_schedules_one_bounded_drain(monkeypatch):
    monkeypatch.setattr(tg_calls, "MAX_NATIVE_PENDING_FRAMES", 3)

    class Loop:
        def __init__(self):
            self.callbacks = []

        def call_soon_threadsafe(self, callback):
            self.callbacks.append(callback)

    loop = Loop()
    box = NativePcmMailbox(loop)
    bridge = SimpleNamespace(rate_hz=48000)
    old, new = TelegramCall(bridge, 42), TelegramCall(bridge, 42)
    box.watch(old)

    def burst():
        for n in range(200):
            box.offer(42, [SimpleNamespace(data=bytes([n]))])

    await asyncio.to_thread(burst)
    assert len(loop.callbacks) == 1
    assert [pcm for _, pcm in box.pending] == [b"\xc5", b"\xc6", b"\xc7"]
    old.remote_ended()
    box.forget(old)
    box.watch(new)
    box.offer(42, [SimpleNamespace(data=b"new")])
    assert len(loop.callbacks) == 1
    loop.callbacks.pop()()
    assert new.inbox.get_nowait() == b"new"
    assert [pcm async for pcm in old.frames()] == []
    assert not box.pending and not box.scheduled


@pytest.mark.parametrize("installable", [True, False])
async def test_native_install_gates_dispatch_and_filters_non_mic(monkeypatch, installable):
    seen = []
    entered = asyncio.Event()
    monkeypatch.setattr(tg_calls, "MAX_NATIVE_PENDING_FRAMES", 2)

    class Binding:
        def __init__(self):
            self.callback = None

        def on_frames(self, fn):
            if self.callback is not None and not installable:
                raise RuntimeError("synthetic native mailbox install failure")
            self.callback = fn

    class Calls:
        def __init__(self, client):
            self._binding = Binding()
            self.status_hook = None
            self.native_ready = False

            def predecessor(*args):
                pytest.fail("unbounded predecessor called")

            self.predecessor = predecessor

        def on_update(self):
            def register(fn):
                assert self.native_ready
                assert self._binding.callback is not None
                assert self._binding.callback is not self.predecessor
                self.status_hook = fn
            return register

        async def start(self):
            assert self.status_hook is None
            self._binding.on_frames(self.predecessor)
            self.native_ready = True

    class Update:
        Status = SimpleNamespace(INCOMING_CALL=1, DISCARDED_CALL=2)

        def __init__(self, chat_id, status):
            self.chat_id, self.status = chat_id, status

    lib = SimpleNamespace(
        PyTgCalls=Calls, ChatUpdate=Update, StreamFrames=type("Frames", (), {}),
        use_native_mic_mailbox=True,
        Direction=SimpleNamespace(from_raw=str, INCOMING="in"),
        Device=SimpleNamespace(from_raw=str, MICROPHONE="mic"),
    )

    async def on_call(call):
        seen.append(call)
        entered.set()
        await asyncio.Event().wait()

    bridge = tg_calls.TelegramCallBridge("fake", on_call, lib=lib)
    if not installable:
        with pytest.raises(RuntimeError, match="synthetic native mailbox install failure"):
            await bridge.start()
        assert bridge.calls.native_ready
        assert bridge.calls.status_hook is None and not bridge.started and bridge.native_pcm is None
        return

    await bridge.start()
    assert bridge.calls.status_hook == bridge.on_update
    await bridge.calls.status_hook(None, Update(42, Update.Status.INCOMING_CALL))
    await asyncio.wait_for(entered.wait(), 1)
    [call] = seen
    emit = bridge.calls._binding.callback
    emit(42, "out", "mic", [SimpleNamespace(data=b"out")])
    emit(42, "in", "camera", [SimpleNamespace(data=b"video")])
    emit(42, "in", "mic", [SimpleNamespace(data=b"voice")])
    await asyncio.sleep(0)
    assert call.inbox.get_nowait() == b"voice" and call.inbox.empty()
    emit(42, "in", "mic", [SimpleNamespace(data=b"cancelled payload")])
    [task] = bridge.tasks
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not bridge.active and not bridge.native_pcm.targets and not bridge.native_pcm.pending


@pytest.mark.parametrize("budget", ["items", "bytes"])
async def test_broker_overload_discards_all_undelivered_events_and_finishes(monkeypatch, budget):
    if budget == "items":
        monkeypatch.setattr(broker_live, "MAX_PENDING_EVENTS", 2)
        monkeypatch.setattr(broker_live, "MAX_PENDING_EVENT_BYTES", 1000)
    else:
        monkeypatch.setattr(broker_live, "MAX_PENDING_EVENTS", 10)
        monkeypatch.setattr(broker_live, "MAX_PENDING_EVENT_BYTES", 60)

    ws = Stream([b"pcm", {"type": "interrupted"}, b"x" * 65, {"type": "done"}])
    session = BrokerLiveSession(ws, {})
    await asyncio.wait_for(session.reader, 1)
    assert session.queue.qsize() == 1   # only the end marker survives
    assert session.queued_event_bytes == 0
    assert [event async for event in session.events()] == []
    assert [event async for event in session.events()] == []
    assert (await asyncio.wait_for(session.finish(), 1)).end_reason == "receive_backlog_overflow"
    assert ws.closed


async def test_valid_broker_stream_keeps_event_order_and_releases_byte_budget():
    ws = Stream([b"pcm", {"type": "transcript", "role": "assistant", "text": "Алло"},
                 {"type": "interrupted"}, {"type": "done", "end_reason": "synthetic"}])
    session = BrokerLiveSession(ws, {})
    await session.reader
    assert session.queued_event_bytes > 0
    assert [event.kind async for event in session.events()] == [AUDIO, TRANSCRIPT, INTERRUPTED]
    assert session.queued_event_bytes == 0
    assert (await session.finish()).end_reason == "synthetic"


@pytest.mark.parametrize("rate", [0, -1, 1_000_000, "not-a-rate", []])
async def test_invalid_negotiated_rate_closes_without_opening_reader(rate):
    ws = Stream(ready={"type": "ready", "output_rate_hz": rate})

    async def connect(url, headers):
        return ws

    client = BrokerLiveVoice("https://fake.invalid", "fake", connect=connect)
    with pytest.raises(LiveVoiceError, match="invalid ready audio metadata"):
        await client.open(voice="Kore", system="")
    assert ws.closed
