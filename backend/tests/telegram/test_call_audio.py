"""Call audio without a database: PCM helpers, the transcript, the audio bridge, the py-tgcalls
backend over a fake library and the runtime hook that attaches it."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from sim.ports import LiveEvent, Peer
from sim.telegram.calls import CallsAttacher
from sim.telegram.calls.backend import CallGone
from sim.telegram.calls.bridge import HUNG_UP, MODEL_DONE, TIME_LIMIT, AudioBridge, Transcript
from sim.telegram.calls.pcm import PcmBuffer, frame_bytes, resample
from sim.telegram.calls.pytgcalls_backend import ANSWER_TIMEOUT_S, PyTgCallsBackend
from tests.fakes.telegram import FakeIncomingCall, FakeLiveSession, FakeTelethonClient, FakeUser

# --- pcm -------------------------------------------------------------------------------------


def test_resample_lengths_and_values():
    pcm48 = b"\x10\x00" * 480                         # 10 ms at 48 kHz
    assert len(resample(pcm48, 48_000, 16_000)) == 160 * 2
    assert resample(pcm48, 48_000, 16_000)[:2] == b"\x10\x00"
    assert len(resample(b"\x00\x00" * 240, 24_000, 48_000)) == 480 * 2
    assert resample(b"\x01", 48_000, 16_000) == b""
    assert resample(b"\x01\x00\x02", 16_000, 16_000) == b"\x01\x00"


def test_pcm_buffer_frames_and_tail():
    buf = PcmBuffer(frame_bytes(48_000))
    assert buf.frame_size == 960
    buf.push(b"\x01" * 1000)
    assert buf.pop_frame() == b"\x01" * 960
    assert buf.pop_frame() is None
    buf.pad_to_frame()
    assert buf.pop_frame() == b"\x01" * 40 + b"\x00" * 920
    buf.pad_to_frame()
    assert len(buf) == 0


# --- transcript ------------------------------------------------------------------------------


def test_transcript_joins_fragments_of_one_speaker():
    t = Transcript()
    t.add("persona", "Привет", 0.04)
    t.add("persona", "! Как", 0.5)
    t.add("persona", " дела?", 0.9)
    t.add("client", "   ", 1.0)
    t.add("client", "Нормально.", 1.23)
    t.add("client", "А ты?", 2.0)
    assert t.result() == [
        {"who": "persona", "text": "Привет! Как дела?", "t": 0.0},
        {"who": "client", "text": "Нормально. А ты?", "t": 1.2},
    ]


# --- bridge ----------------------------------------------------------------------------------


async def test_bridge_ends_when_the_caller_hangs_up_and_sends_audio_up():
    session = FakeLiveSession([LiveEvent("transcript_in", text="алло")])
    call = FakeIncomingCall(frames=[b"\x00\x00" * 960] * 2, hang_up=session.emitted)
    outcome = await AudioBridge(call, session, max_seconds=5).run()
    assert outcome.reason == HUNG_UP and outcome.error is None
    assert outcome.transcript[0]["text"] == "алло"
    assert session.audio_in == [b"\x00\x00" * 640]     # 2 x 20 ms at 16 kHz = one 40 ms chunk


async def test_bridge_plays_the_model_speech_before_hanging_up():
    speech = b"\x05\x00" * 480                          # 20 ms at 24 kHz -> 2 frames at 48 kHz
    session = FakeLiveSession([LiveEvent("audio", audio=speech), LiveEvent("done")], end=True)
    call = FakeIncomingCall(hang_up=asyncio.Event())
    outcome = await AudioBridge(call, session, max_seconds=5).run()
    assert outcome.reason == MODEL_DONE
    assert len(call.played) == outcome.frames_out == 2
    assert all(len(frame) == 960 for frame in call.played)


async def test_bridge_stops_at_the_time_limit():
    session = FakeLiveSession([])
    call = FakeIncomingCall(hang_up=asyncio.Event())
    outcome = await AudioBridge(call, session, max_seconds=0.05).run()
    assert outcome.reason == TIME_LIMIT


async def test_interruption_drops_buffered_speech_and_turn_end_pads_the_tail():
    session = FakeLiveSession([LiveEvent("audio", audio=b"\x01\x00" * 300),
                               LiveEvent("interrupted"),
                               LiveEvent("audio", audio=b"\x01\x00" * 250),
                               LiveEvent("turn_complete")], end=True)
    bridge = AudioBridge(FakeIncomingCall(), session, max_seconds=5)
    await bridge.downlink()
    assert len(bridge.out) == 2 * 960                  # 500 samples at 48 kHz, padded to 2 frames
    assert bytes(bridge.out.data[1000:]) == b"\x00" * 920


async def test_a_failing_side_is_reported_as_a_bridge_error():
    class Broken(FakeLiveSession):
        async def send_audio(self, pcm16k: bytes) -> None:
            raise ConnectionError("socket closed")

    session = Broken([])
    call = FakeIncomingCall(frames=[b"\x00\x00" * 960] * 2, hang_up=asyncio.Event())
    outcome = await AudioBridge(call, session, max_seconds=5).run()
    assert outcome.reason == "bridge_error" and "socket closed" in (outcome.error or "")


# --- py-tgcalls backend over a fake library --------------------------------------------------


class Status:
    INCOMING_CALL = 1
    DISCARDED_CALL = 2
    LEFT_CALL = 4
    BUSY_CALL = 8


class FakeChatUpdate:
    Status = Status

    def __init__(self, chat_id: int, status: int) -> None:
        self.chat_id, self.status = chat_id, status


class FakeFrames:
    def __init__(self, chat_id: int, frames: list[bytes], direction: str = "in",
                 device: str = "mic") -> None:
        self.chat_id, self.direction, self.device = chat_id, direction, device
        self.frames = [SimpleNamespace(frame=f) for f in frames]


class FakeTgCalls:
    def __init__(self, client: Any) -> None:
        self.client = client
        self.handler: Any = None
        self.log: list[Any] = []
        self.hold: asyncio.Event | None = None

    def on_update(self, filters: Any = None) -> Any:
        def register(func: Any) -> Any:
            self.handler = func
            return func
        return register

    async def start(self) -> None:
        self.log.append("start")

    async def play(self, chat_id: int, stream: Any, config: Any) -> None:
        self.log.append(("play", chat_id, stream, config))
        if self.hold is not None:
            await self.hold.wait()

    async def record(self, chat_id: int, stream: Any) -> None:
        self.log.append(("record", chat_id, stream))

    async def send_frame(self, chat_id: int, device: Any, data: bytes) -> None:
        self.log.append(("frame", chat_id, device, data))

    async def leave_call(self, chat_id: int) -> None:
        self.log.append(("leave", chat_id))


LIB = SimpleNamespace(
    PyTgCalls=FakeTgCalls, ChatUpdate=FakeChatUpdate, StreamFrames=FakeFrames,
    Device=SimpleNamespace(MICROPHONE="mic"), Direction=SimpleNamespace(INCOMING="in"),
    ExternalMedia=SimpleNamespace(AUDIO="ext-audio"),
    CallConfig=lambda timeout: ("config", timeout),
    MediaStream=lambda kind, params: ("media", kind, params),
    RecordStream=lambda audio, params: ("record", audio, params),
    AudioParameters=lambda rate, channels: (rate, channels),
)


async def started(handler: Any, client: Any = "client") -> PyTgCallsBackend:
    backend = PyTgCallsBackend(client, lib=LIB)
    await backend.start(handler)
    return backend


async def test_backend_answers_streams_and_ends_on_the_callers_hang_up():
    got: list[bytes] = []

    async def on_call(call):
        await call.answer()
        await call.play(b"OUT")
        async for frame in call.frames():
            got.append(frame)

    backend = await started(on_call)
    await backend.start(on_call)                         # starts py-tgcalls once
    calls = backend.calls
    assert calls.log == ["start"] and calls.handler == backend.on_update
    await backend.on_update(None, FakeChatUpdate(42, Status.INCOMING_CALL))
    await backend.on_update(None, FakeChatUpdate(42, Status.INCOMING_CALL))   # duplicate ring
    await backend.on_update(None, FakeChatUpdate(-100, Status.INCOMING_CALL))  # a group call
    await asyncio.sleep(0.01)
    await backend.on_update(None, FakeFrames(42, [b"A", b"B"]))
    await backend.on_update(None, FakeFrames(42, [b"X"], direction="out"))
    await backend.on_update(None, "something else")
    await backend.on_update(None, FakeChatUpdate(42, Status.DISCARDED_CALL))
    await asyncio.wait_for(asyncio.gather(*backend.tasks), 1)
    assert got == [b"A", b"B"]
    assert ("play", 42, ("media", "ext-audio", (48000, 1)), ("config", ANSWER_TIMEOUT_S)) \
        in calls.log
    assert ("record", 42, ("record", True, (48000, 1))) in calls.log
    assert ("frame", 42, "mic", b"OUT") in calls.log
    assert ("leave", 42) not in calls.log                # the caller hung up first
    assert backend.active == {}


def names(log: list[Any]) -> list[str]:
    return [entry if isinstance(entry, str) else entry[0] for entry in log]


async def ring_and_finish(handler: Any, chat_id: int = 5) -> PyTgCallsBackend:
    backend = await started(handler)
    await backend.on_update(None, FakeChatUpdate(chat_id, Status.INCOMING_CALL))
    await asyncio.wait_for(asyncio.gather(*backend.tasks), 1)
    assert backend.active == {}
    return backend


async def test_backend_declines_a_ringing_call():
    async def decline(call):
        await call.decline()
        assert call.ended

    backend = await ring_and_finish(decline)
    assert backend.calls.log == ["start", ("leave", 5)]


async def test_backend_hangs_up_an_answered_call_once():
    async def talk(call):
        await call.answer()

    backend = await ring_and_finish(talk)
    assert names(backend.calls.log) == ["start", "play", "record", "leave"]


async def test_backend_leaves_an_unanswered_call_alone():
    async def ignore(call):
        return None

    backend = await ring_and_finish(ignore)
    assert backend.calls.log == ["start"]


async def test_a_caller_who_hangs_up_while_she_picks_up_cancels_the_answer():
    failures: list[str] = []

    async def on_call(call):
        try:
            await call.answer()
        except CallGone as exc:
            failures.append(str(exc))

    backend = await started(on_call)
    backend.calls.hold = asyncio.Event()                 # the caller's side never confirms
    await backend.on_update(None, FakeChatUpdate(42, Status.INCOMING_CALL))
    await asyncio.sleep(0.01)
    await backend.on_update(None, FakeChatUpdate(42, Status.DISCARDED_CALL))
    await asyncio.wait_for(asyncio.gather(*backend.tasks), 1)
    assert failures == ["the caller hung up while the call was being picked up"]
    assert names(backend.calls.log) == ["start", "play", "leave"]   # no record, cleaned up


async def test_the_caller_is_resolved_from_the_telethon_cache():
    client = FakeTelethonClient()
    client.entities[42] = FakeUser(id=42, first_name="Аня", last_name=None)
    peers: list[Peer] = []

    async def on_call(call):
        peers.append(await call.peer())

    backend = await started(on_call, client)
    await backend.on_update(None, FakeChatUpdate(42, Status.INCOMING_CALL))
    await asyncio.wait_for(asyncio.gather(*backend.tasks), 1)
    assert peers[0].external_id == "42" and peers[0].name == "Аня"


# --- attaching calls to a connected account -------------------------------------------------


class FakeService:
    def __init__(self) -> None:
        self.handled: list[tuple[int, Any]] = []

    async def handle(self, channel_id: int, call: Any) -> None:
        self.handled.append((channel_id, call))


async def test_attacher_routes_calls_of_the_account_to_the_service():
    service = FakeService()
    backends: list[Any] = []

    class Backend:
        def __init__(self, client: Any) -> None:
            self.client = client
            backends.append(self)

        async def start(self, on_call: Any) -> None:
            self.on_call = on_call

    attacher = CallsAttacher(service, backend_factory=Backend)
    await attacher.attach(7, "client")
    await backends[0].on_call("the-call")
    assert service.handled == [(7, "the-call")]
    assert attacher.backends == {7: backends[0]}


async def test_attacher_failure_only_disables_calls():
    def broken(client: Any) -> Any:
        raise ImportError("ntgcalls missing")

    attacher = CallsAttacher(FakeService(), backend_factory=broken)
    await attacher.attach(7, "client")
    assert attacher.backends == {}


@pytest.mark.parametrize("status", [Status.LEFT_CALL, Status.BUSY_CALL])
async def test_other_end_statuses_end_the_call(status):
    ended = asyncio.Event()

    async def on_call(call):
        async for _ in call.frames():
            pass
        ended.set()

    backend = await started(on_call)
    await backend.on_update(None, FakeChatUpdate(9, Status.INCOMING_CALL))
    await asyncio.sleep(0)
    await backend.on_update(None, FakeChatUpdate(9, status))
    await asyncio.wait_for(ended.wait(), 1)
