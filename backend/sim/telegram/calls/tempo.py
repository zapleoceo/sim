"""Her speech in a call, faster or slower without a change of pitch (`persona.voice_speed`,
docs/calls.md, «Скорость речи»).

The live model has no speed setting. Its audio arrives in bursts faster than real time, so the
call can afford a time-stretch on the way out: one ffmpeg (`atempo`, which keeps the pitch) per
model turn, fed 24 kHz PCM and returning 48 kHz PCM for the call. The end of a turn closes its
input, so her last words come out at once; an interruption kills it, so nothing she was saying
plays after the caller cut in. If ffmpeg can not start, she speaks at the model's pace
(plain resampling) — a call never fails because of the speed.

`Tempo` is what the audio bridge sees; tests use a fake."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sim.domain.speech import clamp_speed, is_normal
from sim.telegram.calls.pcm import resample

log = logging.getLogger(__name__)

READ_BYTES = 4096
FLUSH_TIMEOUT_S = 3.0

Sink = Callable[[bytes], None]
Spawn = Callable[..., Awaitable[Any]]


class Tempo(Protocol):
    async def feed(self, pcm: bytes) -> None:
        """Model audio (input rate) of the current turn."""
        ...

    async def flush(self) -> None:
        """The turn ended: everything fed so far reaches the sink before this returns."""
        ...

    async def reset(self) -> None:
        """She was interrupted: what is still inside is dropped."""
        ...


def tempo_args(speed: float, in_hz: int, out_hz: int, ffmpeg: str = "ffmpeg") -> list[str]:
    """Raw mono s16le in, `atempo` (pitch kept) and the call's rate out, flushed per packet."""
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-fflags", "nobuffer", "-probesize", "32", "-analyzeduration", "0",
            "-f", "s16le", "-ar", str(in_hz), "-ac", "1", "-i", "pipe:0",
            "-af", f"atempo={clamp_speed(speed):.2f}", "-ar", str(out_hz), "-ac", "1",
            "-flush_packets", "1", "-f", "s16le", "pipe:1"]


class FfmpegTempo:
    def __init__(self, speed: float, *, in_hz: int, out_hz: int, sink: Sink,
                 spawn: Spawn = asyncio.create_subprocess_exec, ffmpeg: str = "ffmpeg") -> None:
        self.args = tempo_args(speed, in_hz, out_hz, ffmpeg)
        self.in_hz, self.out_hz = in_hz, out_hz
        self.sink = sink
        self.spawn = spawn
        self.proc: Any = None
        self.reader: asyncio.Task[None] | None = None
        self.broken = False               # ffmpeg failed: the model's own pace from now on

    async def feed(self, pcm: bytes) -> None:
        if not pcm:
            return
        if not self.broken and self.proc is None:
            await self._start()
        if self.broken:
            self.sink(resample(pcm, self.in_hz, self.out_hz))
            return
        try:
            self.proc.stdin.write(pcm)
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            log.warning("call tempo: ffmpeg input closed (%s), plain speed", type(exc).__name__)
            await self.reset()
            self.broken = True
            self.sink(resample(pcm, self.in_hz, self.out_hz))

    async def _start(self) -> None:
        try:
            self.proc = await self.spawn(*self.args, stdin=asyncio.subprocess.PIPE,
                                         stdout=asyncio.subprocess.PIPE,
                                         stderr=asyncio.subprocess.DEVNULL)
        except OSError as exc:
            log.warning("call tempo: ffmpeg did not start (%s), plain speed", exc)
            self.broken, self.proc = True, None
            return
        self.reader = asyncio.ensure_future(self._read(self.proc))

    async def _read(self, proc: Any) -> None:
        while chunk := await proc.stdout.read(READ_BYTES):
            self.sink(chunk)

    async def flush(self) -> None:
        proc, reader = self.proc, self.reader
        self.proc, self.reader = None, None
        if proc is None:
            return
        with contextlib.suppress(Exception):
            proc.stdin.close()
        try:
            if reader is not None:
                await asyncio.wait_for(reader, FLUSH_TIMEOUT_S)
            await asyncio.wait_for(proc.wait(), FLUSH_TIMEOUT_S)
        except Exception:               # a timeout or a dead process: the turn ends anyway
            log.warning("call tempo: ffmpeg did not finish the turn in time")
            _kill(proc)
            if reader is not None:
                reader.cancel()

    async def reset(self) -> None:
        proc, reader = self.proc, self.reader
        self.proc, self.reader = None, None
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        if proc is not None:
            _kill(proc)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), FLUSH_TIMEOUT_S)


def _kill(proc: Any) -> None:
    with contextlib.suppress(ProcessLookupError, OSError):
        proc.kill()


TempoFactory = Callable[[float, int, int, Sink], Tempo | None]


def ffmpeg_tempo(speed: float, in_hz: int, out_hz: int, sink: Sink) -> Tempo | None:
    """None at the model's own pace (1.0): the bridge resamples directly, no process."""
    if is_normal(speed):
        return None
    return FfmpegTempo(speed, in_hz=in_hz, out_hz=out_hz, sink=sink)
