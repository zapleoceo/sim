"""Her live speech on a call: streamed as the model speaks, reviewed after each reply.

The model's audio goes to the call buffer the moment it arrives, so the caller hears her with
the model's own latency and nothing on top. Holding a whole reply back until a model review
approved it made every reply wait for the reply to end AND for a guard call (seconds each):
callers heard «Алло» and then silence, and hung up.

Each finished reply is still reviewed by the guard, in the background, with the call so far as
history. The verdict, its time and the model are steps of «Ход звонка» (`CallTrace.step`) and the
verdict is stored on the reply's message. A reply the guard rejects ends the call at once (no
more of it is heard, the call is journaled as a technical problem); a review that fails
technically is noted and the call goes on.

Delivery is counted by what the transport accepted: the pacer reports every speech frame it
sent (`acknowledge`), and a reply cut by the caller talking over her is recorded as partly heard."""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sim.adapters.pcm import PcmBuffer, resample
from sim.clock import Clock, utcnow
from sim.ports.live_voice import AUDIO, INTERRUPTED, TRANSCRIPT, TURN_COMPLETE, LiveVoiceSession
from sim.services.call_trace import CallTrace, seconds
from sim.services.guard import GuardOutcome
from sim.services.prompt import Turn

REVIEW_TIMEOUT_S = 20.0
QUOTE_MAX = 120
Review = Callable[[list[Turn], str], Awaitable[GuardOutcome]]


def quote(text: str) -> str:
    text = " ".join(text.split())
    return f"«{text[:QUOTE_MAX]}{'…' if len(text) > QUOTE_MAX else ''}»"


class CallContextChanged(Exception):
    """The dialog changed while a reply was reviewed: that verdict is about an old context."""


@dataclass
class PlaybackTurn:
    role: str
    text: str
    guard: GuardOutcome | None = None
    released: bool = False           # her audio went to the call buffer
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
        # Her replies in the call buffer, in order, with the bytes not yet sent.
        self.queued: deque[list] = deque()
        self.reviews: set[asyncio.Task[None]] = set()   # verdicts still on their way

    def acknowledge(self, size: int) -> None:
        """The transport accepted `size` bytes of her speech: they belong to the oldest reply."""
        while size and self.queued:
            entry = self.queued[0]
            turn, left = entry
            used = min(size, left)
            turn.played_bytes += used
            size -= used
            entry[1] = left - used
            if entry[1] == 0:
                self.queued.popleft()

    def queue(self, turn: PlaybackTurn, out: PcmBuffer, pcm: bytes) -> None:
        if not pcm:
            return
        out.push(pcm)
        turn.queued_bytes += len(pcm)
        if self.queued and self.queued[-1][0] is turn:
            self.queued[-1][1] += len(pcm)
        else:
            self.queued.append([turn, len(pcm)])

    def close_turn(self, turn: PlaybackTurn, out: PcmBuffer) -> None:
        """Her reply ended: its last partial frame is padded with silence, so it is sent too."""
        rest = len(out) % out.frame_size
        padding = out.frame_size - rest if rest else 0
        if padding:
            out.push(b"\x00" * padding)
        if padding and self.queued and self.queued[-1][0] is turn:
            turn.queued_bytes += padding
            self.queued[-1][1] += padding

    def drop_queued(self, out: PcmBuffer) -> None:
        out.clear()
        self.queued.clear()

    async def reviewed(self, history: list[Turn], draft: str) -> GuardOutcome:
        async with asyncio.timeout(self.timeout):
            return await self.review(history, draft)

    async def run(self, session: LiveVoiceSession, out: PcmBuffer, rate_hz: int,
                  trace: CallTrace) -> None:
        current: PlaybackTurn | None = None
        text: list[str] = []
        stop = asyncio.Event()

        async def review(turn: PlaybackTurn, history: list[Turn]) -> None:
            started = self.clock()
            said = quote(turn.text)
            try:
                verdict = await self.reviewed(history, turn.text)
            except CallContextChanged:
                trace.step("voice_review_skipped", f"Её реплика {said}: проверка пропущена, "
                           "разговор обновился во время проверки")
                return
            except Exception as exc:
                cause = "не уложилась во время" if isinstance(exc, TimeoutError) else \
                    f"ошибка {type(exc).__name__}"
                trace.step("voice_review_failed", f"Её реплика {said}: проверка {cause} — "
                           "звонок продолжается", error=type(exc).__name__)
                return
            turn.guard = verdict
            took = seconds((self.clock() - started).total_seconds())
            if verdict.allowed and verdict.text == turn.text:
                trace.step("voice_review", f"Её реплика {said}: проверка {took} — разрешена"
                           f"{', модель ' + verdict.model if verdict.model else ''}", allowed=True)
                return
            why = "; ".join(verdict.violations) or "Контролёр изменил голосовую реплику"
            trace.step("voice_review", f"Её реплика {said}: проверка {took} — запрещена ({why}); "
                       "звонок завершается", allowed=False)
            self.failure = self.failure or why
            self.drop_queued(out)
            stop.set()

        async def receive() -> None:
            nonlocal current
            async for event in session.events():
                if event.kind == AUDIO:
                    trace.first("first_model_audio", "Модель начала отвечать голосом",
                                bytes=len(event.pcm))
                    trace.count("model_audio_bytes", len(event.pcm))
                    if current is None:
                        current = PlaybackTurn("assistant", "", released=True)
                        self.turns.append(current)
                    self.queue(current, out, resample(event.pcm, session.output_rate_hz, rate_hz))
                elif event.kind == TRANSCRIPT:
                    if event.role == "assistant":
                        text.append(event.text)
                    elif event.role == "user" and event.text.strip():
                        self.turns.append(PlaybackTurn("user", event.text.strip(), released=True))
                        trace.step("caller_said", f"Клиент: {quote(event.text)}")
                elif event.kind == INTERRUPTED:
                    trace.step("interrupted", "Клиент заговорил — её недоговорённая реплика "
                               "оборвана", dropped_bytes=len(out))
                    trace.count("interruptions")
                    self.drop_queued(out)
                    if current is not None:
                        current.text = "".join(text).strip()
                        current.cancel_reason = "Клиент перебил: реплика прозвучала не полностью."
                    current = None
                    text.clear()
                elif event.kind == TURN_COMPLETE:
                    trace.count("model_turns")
                    if current is not None:
                        self.close_turn(current, out)
                        current.text = "".join(text).strip()
                        history = [Turn(t.role, t.text) for t in self.turns if t is not current]
                        task = asyncio.create_task(review(current, history))
                        self.reviews.add(task)
                        task.add_done_callback(self.reviews.discard)
                    current = None
                    text.clear()
            if current is not None:          # the session ended mid-reply
                current.text = "".join(text).strip()

        reader = asyncio.create_task(receive())
        stopper = asyncio.create_task(stop.wait())
        try:
            done, _ = await asyncio.wait((reader, stopper), return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                await reader
        finally:
            for task in (reader, stopper):
                task.cancel()
            await asyncio.gather(reader, stopper, return_exceptions=True)

    async def settle(self) -> None:
        """After the call: her last verdicts still reach the record (each within `timeout`)."""
        pending = set(self.reviews)
        if pending:
            await asyncio.wait(pending, timeout=self.timeout)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
