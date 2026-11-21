"""The conversation of an answered call: caller audio up to the live model, model speech down
to the call, and the transcript.

Three tasks carry it: `uplink` (caller -> model, resampled to 16 kHz, 40 ms chunks), `downlink`
(model events -> outgoing buffer + transcript) and `pacer` (buffer -> the call, one 10 ms frame
at a time at real speed). The conversation ends when any of them stops: the caller hung up
(uplink), the model finished or failed (downlink), or `max_seconds` passed. After a clean model
finish her last buffered words still play (at most `DRAIN_LIMIT_S`)."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sim.ports import LiveSession
from sim.telegram.calls.backend import IncomingCall
from sim.telegram.calls.pcm import FRAME_MS, PcmBuffer, frame_bytes, resample

log = logging.getLogger(__name__)

LIVE_IN_HZ = 16_000              # to the live model
LIVE_OUT_HZ = 24_000             # from the live model
UPLINK_MS = 40
DRAIN_LIMIT_S = 10.0
MAX_LAG_S = 0.1                  # a pacer later than this restarts its schedule, no burst

CLIENT = "client"
PERSONA = "persona"
SPEAKER_OF = {"transcript_in": CLIENT, "transcript_out": PERSONA}

# End reasons (Call.error is filled for the failing ones).
HUNG_UP = "hung_up"
MODEL_DONE = "model_done"
MODEL_ERROR = "model_error"
TIME_LIMIT = "time_limit"
BRIDGE_ERROR = "bridge_error"


@dataclass
class Transcript:
    """[{who, text, t}] — consecutive fragments of one speaker are joined into one line (the
    live model sends speech recognition in pieces as it hears it)."""

    lines: list[dict[str, Any]] = field(default_factory=list)

    def add(self, who: str, text: str, t: float) -> None:
        if not text.strip():
            return
        if self.lines and self.lines[-1]["who"] == who:
            last = self.lines[-1]["text"]
            glue = " " if last[-1:] in (".", "!", "?", "…") and text[:1].isalnum() else ""
            self.lines[-1]["text"] = last + glue + text
            return
        self.lines.append({"who": who, "text": text, "t": round(max(0.0, t), 1)})

    def result(self) -> list[dict[str, Any]]:
        return [{**line, "text": " ".join(line["text"].split())} for line in self.lines
                if line["text"].strip()]


@dataclass
class Outcome:
    reason: str
    error: str | None
    transcript: list[dict[str, Any]]
    frames_out: int = 0


class AudioBridge:
    def __init__(
        self, call: IncomingCall, session: LiveSession, *, max_seconds: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.call = call
        self.session = session
        self.max_seconds = max_seconds
        self.sleep = sleep
        self.mono = monotonic
        self.out = PcmBuffer(frame_bytes(call.rate_hz))
        self.transcript = Transcript()
        self.started = monotonic()
        self.model_error: str | None = None
        self.frames_out = 0

    async def run(self) -> Outcome:
        tasks = {
            "uplink": asyncio.ensure_future(self.uplink()),
            "downlink": asyncio.ensure_future(self.downlink()),
            "pacer": asyncio.ensure_future(self.pace()),
        }
        reason, error = TIME_LIMIT, None
        try:
            done, _ = await asyncio.wait(tasks.values(), timeout=self.max_seconds,
                                         return_when=asyncio.FIRST_COMPLETED)
            failed = [t for t in done if not t.cancelled() and t.exception() is not None]
            if failed:
                exc = failed[0].exception()
                reason, error = BRIDGE_ERROR, f"{type(exc).__name__}: {str(exc)[:300]}"
            elif tasks["downlink"] in done:
                reason = MODEL_ERROR if self.model_error else MODEL_DONE
                error = self.model_error
                if not self.model_error:
                    await self.drain()           # her last words before hanging up
            elif tasks["uplink"] in done:
                reason = HUNG_UP
        finally:
            for task in tasks.values():
                task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
        return Outcome(reason, error, self.transcript.result(), self.frames_out)

    def elapsed(self) -> float:
        return self.mono() - self.started

    async def uplink(self) -> None:
        chunk = frame_bytes(LIVE_IN_HZ, UPLINK_MS)
        pending = bytearray()
        async for frame in self.call.frames():
            pending.extend(resample(frame, self.call.rate_hz, LIVE_IN_HZ))
            if len(pending) >= chunk:
                await self.session.send_audio(bytes(pending))
                pending.clear()

    async def downlink(self) -> None:
        async for event in self.session.events():
            if event.kind == "audio":
                self.out.push(resample(event.audio, LIVE_OUT_HZ, self.call.rate_hz))
            elif event.kind in SPEAKER_OF:
                self.transcript.add(SPEAKER_OF[event.kind], event.text, self.elapsed())
            elif event.kind == "interrupted":
                self.out.clear()                 # the caller talks: stop her mid-sentence
            elif event.kind == "turn_complete":
                self.out.pad_to_frame()
            elif event.kind == "error":
                self.model_error = event.text or "live model error"
                return
            elif event.kind == "done":
                return

    async def pace(self) -> None:
        """One frame every 10 ms while she speaks. The schedule is absolute, so a late wake-up
        is made up by a shorter next sleep; after a stall longer than MAX_LAG_S it restarts
        instead of sending the missed frames in one burst."""
        step = FRAME_MS / 1000
        due = self.mono()
        while True:
            frame = self.out.pop_frame()
            now = self.mono()
            if frame is None:
                due = now + step
                await self.sleep(step)
                continue
            if now - due > MAX_LAG_S:
                due = now
            await self.call.play(frame)
            self.frames_out += 1
            due += step
            await self.sleep(max(0.0, due - self.mono()))

    async def drain(self) -> None:
        deadline = self.mono() + DRAIN_LIMIT_S
        while len(self.out) >= self.out.frame_size and self.mono() < deadline:
            await self.sleep(FRAME_MS / 1000)
