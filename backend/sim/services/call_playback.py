"""Bounded turn audio gate. Receiving interruptions never waits on a model review.

Each finished reply of the model is reviewed before a byte of it plays; the review, its time and
its verdict become steps of the call's «Ход звонка» (`CallTrace.step`). Two exceptions keep the
call alive and quick without loosening the gate:
- the first reply, when it is exactly the fixed «Алло» the greeting asks for, plays without a
  model review: there is nothing in it to check, and the caller hears her at once;
- a review that fails technically (error, timeout) drops only that reply, unheard; the call
  goes on. `MAX_REVIEW_FAILURES` such failures in a row end it, as does any real verdict
  against a reply."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sim.adapters.pcm import PcmBuffer, resample
from sim.clock import Clock, utcnow
from sim.ports.live_voice import AUDIO, INTERRUPTED, TRANSCRIPT, TURN_COMPLETE, LiveVoiceSession
from sim.services.call_trace import CallTrace, seconds
from sim.services.guard import BLOCKED, OK, GuardOutcome
from sim.services.prompt import Turn

REVIEW_TIMEOUT_S = 20.0
MAX_TURN_SECONDS = 30
MAX_WAITING_TURNS = 2
MAX_REVIEW_FAILURES = 2
HELLO = "алло"
QUOTE_MAX = 120
REVIEW_FAILED = "Проверка ответа не завершилась. Ответ не прозвучал."
Review = Callable[[list[Turn], str], Awaitable[GuardOutcome]]


def is_hello(text: str) -> bool:
    """The fixed first word of a call, whatever the punctuation and case."""
    return re.sub(r"[^\w]+", "", text.lower()) == HELLO


def quote(text: str) -> str:
    text = " ".join(text.split())
    return f"«{text[:QUOTE_MAX]}{'…' if len(text) > QUOTE_MAX else ''}»"


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
        self.review_failures = 0          # technical review failures in a row
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

    async def reviewed(self, history: list[Turn], draft: str) -> GuardOutcome:
        """The review within `timeout`; one that straddles a minute boundary is redone, since
        the reply may name the time. Raises on a technical failure (TimeoutError included)."""
        async with asyncio.timeout(self.timeout):
            for attempt in range(2):
                reviewed_at = self.clock().replace(second=0, microsecond=0)
                verdict = await self.review(history, draft)
                if not verdict.allowed or verdict.text != draft:
                    return verdict
                if self.clock().replace(second=0, microsecond=0) == reviewed_at:
                    return verdict
                if attempt == 1:
                    return GuardOutcome(BLOCKED, draft, ["Clock changed during both reviews"])
        raise AssertionError("unreachable")  # pragma: no cover

    async def run(self, session: LiveVoiceSession, out: PcmBuffer, rate_hz: int,
                  trace: CallTrace) -> None:
        pending = bytearray()
        text: list[str] = []
        checks: set[asyncio.Task[None]] = set()
        serial = asyncio.Lock()
        failed = asyncio.Event()

        def reject(reason: str) -> None:
            trace.step("playback_stopped", f"Звонок завершается: {reason}")
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
                said = quote(turn.text)
                history = [Turn(t.role, t.text) for t in self.turns[:before]
                           if t.role == "user" or t.delivered]
                first = not any(t.role == "assistant" for t in self.turns[:before])
                try:
                    if first and is_hello(turn.text):
                        verdict = GuardOutcome(OK, turn.text)
                        trace.step("voice_review", f"Её реплика {said}: фиксированное приветствие, "
                                   "звучит без проверки", skipped=True)
                    else:
                        verdict = await self.reviewed(history, turn.text)
                except CallContextChanged:
                    turn.cancel_reason = "Разговор обновился. Старый ответ отменён."
                    trace.step("voice_review_cancelled", f"Её реплика {said} отменена: во время "
                               "проверки разговор обновился", reason="context_changed")
                    return
                except Exception as exc:
                    if epoch != self.epoch or failed.is_set():
                        return
                    took = seconds((self.clock() - started).total_seconds())
                    cause = "не уложилась во время" if isinstance(exc, TimeoutError) else \
                        f"ошибка {type(exc).__name__}"
                    turn.guard = GuardOutcome(BLOCKED, turn.text, [REVIEW_FAILED])
                    turn.cancel_reason = REVIEW_FAILED
                    self.review_failures += 1
                    trace.step("voice_review_failed", f"Её реплика {said}: проверка {cause} "
                               f"({took}) — реплика не прозвучала, звонок продолжается",
                               error=type(exc).__name__)
                    if self.review_failures >= MAX_REVIEW_FAILURES:
                        reject(f"Проверка ответа не завершилась {self.review_failures} раза подряд")
                    return
                turn.guard = verdict
                if epoch != self.epoch or failed.is_set():
                    return
                self.review_failures = 0
                took = seconds((self.clock() - started).total_seconds())
                if not verdict.allowed or verdict.text != turn.text:
                    why = "; ".join(verdict.violations) or "Контролёр изменил голосовую реплику"
                    trace.step("voice_review", f"Её реплика {said}: проверка {took} — "
                               f"запрещена ({why})", allowed=False)
                    reject(why)
                    return
                if not (first and is_hello(turn.text)):
                    trace.step("voice_review", f"Её реплика {said}: проверка {took} — разрешена"
                               f"{', модель ' + verdict.model if verdict.model else ''}",
                               allowed=True)
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
                    trace.first("first_model_audio", "Модель начала отвечать голосом "
                                "(звук ждёт проверки реплики)", bytes=len(event.pcm))
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
                        trace.step("caller_said", f"Клиент: {quote(event.text)}")
                elif event.kind == INTERRUPTED:
                    self.epoch += 1
                    trace.step("interrupted", "Клиент заговорил — её недоговорённая реплика "
                               "сброшена", dropped_bytes=len(out) + len(pending))
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
