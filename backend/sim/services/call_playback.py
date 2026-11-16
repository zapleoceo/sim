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


@dataclass
class PlaybackTurn:
    role: str
    text: str
    guard: GuardOutcome | None = None
    released: bool = False


class CallPlayback:
    def __init__(self, review: Review, *, clock: Clock = utcnow,
                 timeout: float = REVIEW_TIMEOUT_S) -> None:
        self.review, self.clock, self.timeout = review, clock, timeout
        self.turns: list[PlaybackTurn] = []
        self.failure = ""
        self.epoch = 0

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
            failed.set()

        async def check(audio: bytes, turn: PlaybackTurn, epoch: int, before: int) -> None:
            async with serial:
                if epoch != self.epoch or failed.is_set():
                    return
                # Review immediately before this turn can start, not behind old queued speech.
                try:
                    async with asyncio.timeout(MAX_TURN_SECONDS + 1):
                        while len(out) and epoch == self.epoch and not failed.is_set():
                            await asyncio.sleep(0.01)
                except TimeoutError:
                    reject("Воспроизведение предыдущей реплики не завершилось")
                    return
                if epoch != self.epoch or failed.is_set():
                    return
                started = self.clock()
                history = [Turn(t.role, t.text) for t in self.turns[:before]
                           if t.role == "user" or t.released]
                try:
                    async with asyncio.timeout(self.timeout):
                        verdict = await self.review(history, turn.text)
                except Exception:
                    verdict = GuardOutcome(BLOCKED, turn.text, ["Проверка голоса не завершена вовремя"])
                turn.guard = verdict
                if epoch != self.epoch or failed.is_set():
                    return
                # A minute/date/DST boundary makes a clock assertion stale. Do not play it.
                if self.clock().replace(second=0, microsecond=0) != started.replace(second=0, microsecond=0):
                    verdict = GuardOutcome(BLOCKED, turn.text, ["Время изменилось во время проверки голоса"])
                    turn.guard = verdict
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
