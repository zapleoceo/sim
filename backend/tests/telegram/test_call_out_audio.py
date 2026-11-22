"""Call audio for her own calls and her speed, without a database: the ffmpeg tempo over a fake
process, the audio bridge with a tempo and a recorder, the call recorder, `dial` / `ring` of the
py-tgcalls backend over a fake library and `CallsAttacher.dial`."""
from __future__ import annotations

import asyncio
from array import array
from types import SimpleNamespace
from typing import Any

import pytest

from sim.ports import LiveEvent, Peer
from sim.telegram.calls import CallsAttacher
from sim.telegram.calls.backend import NoAnswer
from sim.telegram.calls.bridge import MODEL_DONE, AudioBridge
from sim.telegram.calls.pcm import resample
from sim.telegram.calls.pytgcalls_backend import PyTgCallsBackend
from sim.telegram.calls.recording import RECORD_HZ, CallRecorder
from sim.telegram.calls.tempo import FfmpegTempo, ffmpeg_tempo, tempo_args
from tests.fakes.telegram import FakeIncomingCall, FakeLiveSession, FakeTempoFactory
from tests.telegram.test_call_audio import LIB, FakeTgCalls

SPEECH_24K = b"\x05\x00" * 480                     # 20 ms of the model at 24 kHz

# --- tempo -------------------------------------------------------------------------------------


def test_tempo_args_stretch_raw_pcm_between_the_rates() -> None:
    args = tempo_args(1.3, 24_000, 48_000)
    assert args[0] == "ffmpeg" and args[-1] == "pipe:1"
    assert "atempo=1.30" in args
    assert args.index("24000") < args.index("-i") < args.index("48000")
    assert args[args.index("-i") + 1] == "pipe:0"
    # The form's range holds whatever is stored.
    assert "atempo=1.50" in tempo_args(3.0, 24_000, 48_000)
    assert "atempo=0.80" in tempo_args(0.5, 24_000, 48_000)


def test_the_models_own_pace_needs_no_process() -> None:
    sink: list[bytes] = []
    assert ffmpeg_tempo(1.0, 24_000, 48_000, sink.append) is None
    assert ffmpeg_tempo(1.004, 24_000, 48_000, sink.append) is None
    tempo = ffmpeg_tempo(1.3, 24_000, 48_000, sink.append)
    assert isinstance(tempo, FfmpegTempo) and "atempo=1.30" in tempo.args
    assert tempo.proc is None                           # nothing starts before her first words


class FakeStdin:
    def __init__(self, proc: FakeProcess) -> None:
        self.proc = proc
        self.closed = False

    def write(self, data: bytes) -> None:
        if self.proc.broken:
            raise BrokenPipeError("ffmpeg died")
        self.proc.got.append(data)
        self.proc.out.put_nowait(data)                  # passes it through unchanged

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True
        self.proc.out.put_nowait(b"")                   # end of input: end of output


class FakeStdout:
    def __init__(self, queue: asyncio.Queue[bytes]) -> None:
        self.queue = queue

    async def read(self, size: int) -> bytes:
        return await self.queue.get()


class FakeProcess:
    def __init__(self, *, broken: bool = False) -> None:
        self.out: asyncio.Queue[bytes] = asyncio.Queue()
        self.got: list[bytes] = []
        self.broken = broken
        self.killed = False
        self.stdin = FakeStdin(self)
        self.stdout = FakeStdout(self.out)

    def kill(self) -> None:
        self.killed = True
        self.out.put_nowait(b"")

    async def wait(self) -> int:
        return 0


class Spawner:
    def __init__(self, *, error: Exception | None = None, broken: bool = False) -> None:
        self.error = error
        self.broken = broken
        self.calls: list[tuple[Any, ...]] = []
        self.procs: list[FakeProcess] = []

    async def __call__(self, *args: Any, **kwargs: Any) -> FakeProcess:
        self.calls.append(args)
        if self.error is not None:
            raise self.error
        proc = FakeProcess(broken=self.broken)
        self.procs.append(proc)
        return proc


def a_tempo(spawn: Spawner, sink: list[bytes]) -> FfmpegTempo:
    return FfmpegTempo(1.3, in_hz=24_000, out_hz=48_000, sink=sink.append,
                       spawn=spawn)


async def test_one_process_per_turn_and_the_turn_end_flushes_it() -> None:
    spawn, sink = Spawner(), []
    tempo = a_tempo(spawn, sink)

    await tempo.feed(b"ab")
    await tempo.feed(b"cd")
    await tempo.feed(b"")                               # nothing to stretch
    await tempo.flush()

    assert spawn.calls == [tuple(tempo_args(1.3, 24_000, 48_000))]
    assert b"".join(sink) == b"abcd"                    # all of it before flush returns
    assert spawn.procs[0].stdin.closed and tempo.proc is None
    await tempo.flush()                                 # no turn: nothing to do

    await tempo.feed(b"ef")                             # the next turn: a new process
    assert len(spawn.calls) == 2


async def test_an_interruption_kills_the_turn() -> None:
    spawn, sink = Spawner(), []
    tempo = a_tempo(spawn, sink)
    await tempo.feed(b"ab")
    await tempo.reset()
    assert spawn.procs[0].killed and tempo.proc is None and tempo.reader is None
    await tempo.reset()                                 # twice is fine
    await tempo.feed(b"cd")
    assert len(spawn.calls) == 2


async def test_without_ffmpeg_she_speaks_at_the_models_pace() -> None:
    spawn, sink = Spawner(error=FileNotFoundError("ffmpeg")), []
    tempo = a_tempo(spawn, sink)
    await tempo.feed(SPEECH_24K)
    await tempo.feed(SPEECH_24K)
    assert tempo.broken and len(spawn.calls) == 1       # not tried again in this call
    assert sink == [resample(SPEECH_24K, 24_000, 48_000)] * 2
    await tempo.flush()


async def test_a_dead_ffmpeg_falls_back_to_plain_resampling() -> None:
    spawn, sink = Spawner(broken=True), []
    tempo = a_tempo(spawn, sink)
    await tempo.feed(SPEECH_24K)
    assert tempo.broken and spawn.procs[0].killed
    assert sink == [resample(SPEECH_24K, 24_000, 48_000)]


# --- the bridge with a tempo -------------------------------------------------------------------


async def test_the_bridge_feeds_her_audio_through_the_tempo() -> None:
    later = b"\x07\x00" * 100
    session = FakeLiveSession([LiveEvent("audio", audio=SPEECH_24K),
                               LiveEvent("turn_complete"),
                               LiveEvent("audio", audio=b"\x06\x00" * 100),
                               LiveEvent("interrupted"),
                               LiveEvent("audio", audio=later)], end=True)
    factory = FakeTempoFactory()
    bridge = AudioBridge(FakeIncomingCall(), session, max_seconds=5, speed=1.3, tempo=factory)
    [tempo] = factory.made
    assert tempo.args == (1.3, 24_000, 48_000)

    await bridge.downlink()

    assert tempo.log == ["feed", "flush", "feed", "reset", "feed"]
    assert len(bridge.out) == 0                         # the interruption dropped the rest
    assert bytes(tempo.pending) == later                # the new turn is still in the tempo


async def test_the_bridge_flushes_her_last_words_then_resets_the_tempo() -> None:
    session = FakeLiveSession([LiveEvent("audio", audio=SPEECH_24K), LiveEvent("done")],
                              end=True)
    call = FakeIncomingCall(hang_up=asyncio.Event())
    factory = FakeTempoFactory()
    outcome = await AudioBridge(call, session, max_seconds=5, speed=1.3, tempo=factory).run()
    assert outcome.reason == MODEL_DONE
    assert factory.made[0].log == ["feed", "flush", "reset"]
    assert len(call.played) == outcome.frames_out == 2   # 20 ms at 48 kHz, 10 ms frames


async def test_without_a_tempo_the_bridge_resamples_itself() -> None:
    call, session = FakeIncomingCall(), FakeLiveSession([])
    assert AudioBridge(call, session, max_seconds=5).tempo is None
    assert AudioBridge(call, session, max_seconds=5, speed=1.0, tempo=ffmpeg_tempo).tempo is None
    stretched = AudioBridge(call, session, max_seconds=5, speed=1.3, tempo=ffmpeg_tempo)
    assert isinstance(stretched.tempo, FfmpegTempo)


# --- the recording -----------------------------------------------------------------------------


def samples(pcm: bytes) -> list[int]:
    return list(array("h", pcm))


def test_the_recorder_places_each_side_by_time_in_stereo() -> None:
    recorder = CallRecorder()
    recorder.client(b"\x01\x00" * 480, 48_000, 0.0)     # 10 ms at 48 kHz -> 160 samples
    recorder.persona(b"\x02\x00" * 480, 48_000, 0.02)   # heard 20 ms later
    assert recorder.rate_hz == RECORD_HZ == 16_000
    assert [len(track) for track in recorder.sides.values()] == [160, 480]
    assert recorder.seconds == pytest.approx(0.03)

    stereo = samples(recorder.stereo())
    assert len(stereo) == 2 * 480                       # both channels as long as the longest
    left, right = stereo[0::2], stereo[1::2]
    assert set(left[:160]) == {1} and set(left[160:]) == {0}
    assert set(right[:320]) == {0} and set(right[320:]) == {2}


def test_a_late_frame_never_overwrites_and_silence_is_empty() -> None:
    recorder = CallRecorder()
    assert recorder.stereo() == b"" and recorder.seconds == 0.0
    recorder.client(b"\x01\x00" * 480, 48_000, 0.0)
    recorder.client(b"\x03\x00" * 480, 48_000, 0.0)     # same moment: goes after the first
    assert len(recorder.sides["client"]) == 320
    assert samples(recorder.stereo())[0::2][160:] == [3] * 160


async def test_the_bridge_records_what_both_sides_heard() -> None:
    session = FakeLiveSession([LiveEvent("audio", audio=SPEECH_24K), LiveEvent("done")],
                              end=True)
    call = FakeIncomingCall(frames=[b"\x00\x00" * 960] * 2, hang_up=asyncio.Event())
    recorder = CallRecorder()
    outcome = await AudioBridge(call, session, max_seconds=5, recorder=recorder).run()
    assert outcome.frames_out == 2
    assert len(recorder.sides["client"]) >= 2 * 320     # two 20 ms frames of the caller
    assert len(recorder.sides["persona"]) >= 2 * 160    # her two played 10 ms frames
    assert recorder.stereo()


# --- py-tgcalls: her call ----------------------------------------------------------------------


class TimedOutAnswer(Exception):
    pass


class CallBusy(Exception):
    pass


class CallDeclined(Exception):
    pass


class CallDiscarded(Exception):
    pass


class RingingTgCalls(FakeTgCalls):
    """`play` of her call raises what py-tgcalls raises when nobody picks up."""

    error: Exception | None = None

    async def play(self, chat_id: int, stream: Any, config: Any) -> None:
        await super().play(chat_id, stream, config)
        if self.error is not None:
            raise self.error


OUT_LIB = SimpleNamespace(**{**vars(LIB), "PyTgCalls": RingingTgCalls,
                             "TimedOutAnswer": TimedOutAnswer, "CallBusy": CallBusy,
                             "CallDeclined": CallDeclined, "CallDiscarded": CallDiscarded})


def names(log: list[Any]) -> list[str]:
    return [entry if isinstance(entry, str) else entry[0] for entry in log]


async def test_dial_then_ring_until_picked_up() -> None:
    backend = PyTgCallsBackend("client", lib=OUT_LIB)
    call = await backend.dial(Peer(external_id="42"))
    assert backend.active == {42: call} and not call.ended
    assert backend.calls.log == []                      # nothing rings before `ring`

    await call.ring(40)

    assert ("play", 42, ("media", "ext-audio", (48000, 1)), ("config", 40)) in backend.calls.log
    assert ("record", 42, ("record", True, (48000, 1))) in backend.calls.log
    with pytest.raises(NoAnswer) as busy:               # one call with a person at a time
        await backend.dial(Peer(external_id="42"))
    assert busy.value.reason == "busy"
    await call.hangup()
    assert ("leave", 42) in backend.calls.log and backend.active == {}


@pytest.mark.parametrize(("error", "reason"), [
    (TimedOutAnswer, "no_answer"),
    (CallBusy, "busy"),
    (CallDeclined, "declined"),
    (CallDiscarded, "declined"),
])
async def test_nobody_picks_up_is_no_answer(error: type[Exception], reason: str) -> None:
    backend = PyTgCallsBackend("client", lib=OUT_LIB)
    call = await backend.dial(Peer(external_id="42", access_hash="555"))
    backend.calls.error = error()

    with pytest.raises(NoAnswer) as info:
        await call.ring(40)

    assert info.value.reason == reason
    assert call.ended and backend.active == {}
    assert names(backend.calls.log) == ["play", "leave"]


# --- attaching: her calls go through the account's backend --------------------------------------


class DialingService:
    def __init__(self) -> None:
        self.dialled: list[tuple[int, dict[int, Any]]] = []

    async def dial(self, contact_id: int, backends: dict[int, Any]) -> None:
        self.dialled.append((contact_id, dict(backends)))


async def test_attacher_dials_through_the_backends_of_its_accounts() -> None:
    service = DialingService()

    class Backend:
        def __init__(self, client: Any) -> None:
            self.client = client

        async def start(self, on_call: Any) -> None:
            return None

    attacher = CallsAttacher(service, backend_factory=Backend)  # type: ignore[arg-type]
    await attacher.attach(7, "client")
    await attacher.dial(3)
    assert service.dialled == [(3, {7: attacher.backends[7]})]
