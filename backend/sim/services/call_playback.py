"""Bounded turn audio gate. Receiving interruptions never waits on a model review."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sim.adapters.pcm import PcmBuffer, resample
from sim.clock import Clock, utcnow
from sim.ports.live_voice import AUDIO, INTERRUPTED, TRANSCRIPT, TURN_COMPLETE, LiveVoiceSession
from sim.services.call_trace import CallTrace
from sim.services.guard import BLOCKED, GuardOutcome
from sim.services.prompt import Turn

REVIEW_TIMEOUT_S = 20.0
MAX_TURN_SECONDS = 30
MAX_WAITING_TURNS = 2
Review = Callable[[list[Turn], str], Awaitable[GuardOutcome]]


class CallContextChanged(Exception):
    """A normal conversation update invalidates one candidate, not the owner's AI policy."""


@dataclass
class PlaybackTurn:
    role: str
    text: str
    guard: GuardOutcome | None = None
    released: bool = False
    queued_bytes: int = 0
    played_bytes: int = 0
    cancel_reason: str = ""

    @property
    def delivered(self) -> bool:
        return self.released and self.played_bytes >= self.queued_bytes


class CallPlayback:
    def __init__(self, review: Review, *, clock: Clock = utcnow,
                 timeout: float = REVIEW_TIMEOUT_S) -> None:
        self.review, self.clock, self.timeout = review, clock, timeout
        self.turns: list[PlaybackTurn] = []
        self.failure = ""
        self.epoch = 0
        self.playing: PlaybackTurn | None = None
        self.drained = asyncio.Event()
        self.drained.set()

    def cancel_playback(self) -> None:
        self.playing = None
        self.drained.set()

    def acknowledge(self, turn: PlaybackTurn, size: int) -> None:
        """Only called after the transport accepts a speech frame, never on enqueue."""
        turn.played_bytes += size
        if turn.delivered and self.playing is turn:
            self.cancel_playback()

    async def run(self, session: LiveVoiceSession, out: PcmBuffer, rate_hz: int,
                  trace: CallTrace) -> None:
        pending = bytearray()
        text: list[str] = []
        checks: set[asyncio.Task[None]] = set()
        serial = asyncio.Lock()
        failed = asyncio.Event()

        def reject(reason: str) -> None:
            self.failure = reason
            out.clear()
            self.cancel_playback()
            failed.set()

        async def check(audio: bytes, turn: PlaybackTurn, epoch: int, before: int) -> None:
            async with serial:
                if epoch != self.epoch or failed.is_set():
                    return
                # Review immediately before this turn can start, not behind old queued speech.
                try:
                    async with asyncio.timeout(MAX_TURN_SECONDS + 1):
                        await self.drained.wait()
                except TimeoutError:
                    reject("Воспроизведение предыдущей реплики не завершилось")
                    return
                if epoch != self.epoch or failed.is_set():
                    return
                started = self.clock()
                history = [Turn(t.role, t.text) for t in self.turns[:before]
                           if t.role == "user" or t.delivered]
                try:
                    async with asyncio.timeout(self.timeout):
                        for attempt in range(2):
                            reviewed_at = self.clock().replace(second=0, microsecond=0)
                            verdict = await self.review(history, turn.text)
                            if not verdict.allowed or verdict.text != turn.text:
                                break
                            if self.clock().replace(second=0, microsecond=0) == reviewed_at:
                                break
                            if attempt == 1:
                                verdict = GuardOutcome(BLOCKED, turn.text, ["Clock changed during both reviews"])
                except CallContextChanged:
                    turn.cancel_reason = "Разговор обновился. Старый ответ отменён."
                    trace.event("voice_review_cancelled", reason="context_changed")
                    return
                except Exception:
                    verdict = GuardOutcome(BLOCKED, turn.text, ["Проверка ответа не завершилась. Ответ не прозвучал."])
                turn.guard = verdict
                if epoch != self.epoch or failed.is_set():
                    return
                trace.event("voice_review", elapsed_ms=int((self.clock() - started).total_seconds() * 1000),
                            allowed=verdict.allowed, unchanged=verdict.text == turn.text)
                if not verdict.allowed or verdict.text != turn.text:
                    reject("; ".join(verdict.violations) or "Контролёр изменил голосовую реплику")
                    return
                # No await between the interruption fence and releasing approved bytes.
                out.push(resample(audio, session.output_rate_hz, rate_hz))
                tail = out.flush_tail()
                if tail:
                    out.push(tail)
                turn.released = True
                turn.queued_bytes = len(out)
                self.playing = turn
                self.drained.clear()

        async def receive() -> None:
            async for event in session.events():
                if failed.is_set():
                    break
                if event.kind == AUDIO:
                    trace.first("first_model_audio", bytes=len(event.pcm))
                    trace.count("model_audio_bytes", len(event.pcm))
                    pending.extend(event.pcm)
                    if len(pending) > session.output_rate_hz * 2 * MAX_TURN_SECONDS:
                        reject("Голосовая реплика превышает безопасный размер буфера")
                        break
                elif event.kind == TRANSCRIPT:
                    if event.role == "assistant":
                        text.append(event.text)
                        if sum(map(len, text)) > 8000:
                            reject("Транскрипт голосовой реплики превышает лимит")
                            break
                    elif event.role == "user" and event.text.strip():
                        self.turns.append(PlaybackTurn("user", event.text.strip(), released=True))
                elif event.kind == INTERRUPTED:
                    self.epoch += 1
                    trace.event("interrupted", dropped_bytes=len(out) + len(pending))
                    trace.count("interruptions")
                    pending.clear()
                    text.clear()
                    out.clear()
                    self.cancel_playback()
                    for task in checks:
                        task.cancel()
                elif event.kind == TURN_COMPLETE:
                    trace.count("model_turns")
                    draft = "".join(text).strip()
                    if pending:
                        turn = PlaybackTurn("assistant", draft)
                        before = len(self.turns)
                        self.turns.append(turn)
                        if not draft:
                            reject("Нет транскрипта голосовой реплики для проверки")
                            break
                        if sum(not task.done() for task in checks) >= MAX_WAITING_TURNS:
                            reject("Очередь проверки голосовых реплик переполнена")
                            break
                        task = asyncio.create_task(check(bytes(pending), turn, self.epoch, before))
                        checks.add(task)
                        task.add_done_callback(checks.discard)
                    pending.clear()
                    text.clear()
            # EOF with an unfinished turn never releases its audio.
            if pending and not failed.is_set():
                reject("Голосовая реплика не завершена")
            if checks:
                await asyncio.gather(*checks, return_exceptions=True)

        reader = asyncio.create_task(receive())
        stopper = asyncio.create_task(failed.wait())
        try:
            done, _ = await asyncio.wait((reader, stopper), return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                await reader
        finally:
            reader.cancel()
            stopper.cancel()
            for task in checks:
                task.cancel()
            await asyncio.gather(reader, stopper, *checks, return_exceptions=True)
            pending.clear()
