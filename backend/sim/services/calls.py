"""Persona phone calls: an incoming Telegram call answered by the persona's live voice.

  call rings → prepare (who calls, may she answer, her call prompt) → open the Broker live
  session (Gemini Live) → answer → caller audio up, model speech down, paced at real time →
  hang up → the transcript lands in the dialog.

Three tasks carry an answered call: `uplink` (caller → model), `downlink` (model → buffer) and
`pace` (buffer → the call, one frame per 10 ms). The call ends when any of them stops; a task
that stopped on an error is logged, never swallowed: a dead pacer is a silent call.

She answers only a client she already chats with (an existing dialog of that channel), whose
dialog is not taken over by an operator, and only when her `accept_calls` is on and she has a
voice. Otherwise the call is left alone and keeps ringing for a human on the account's devices.
The Broker session is opened BEFORE answering: when the Broker refuses, nobody picked up.

No database transaction is open while the call runs; the transcript is written afterwards in a
short one, like any inbound message."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.adapters.channels.capabilities import IncomingCall
from sim.adapters.db.locks import NS_DIALOG, lock
from sim.adapters.db.models import Message
from sim.adapters.db.repositories.catalog import ChannelRepo, PersonaRepo
from sim.adapters.db.repositories.conversations import DialogRepo
from sim.adapters.db.session import transaction
from sim.adapters.pcm import FRAME_MS, PcmBuffer, frame_bytes, resample
from sim.clock import Clock, utcnow
from sim.domain.enums import Author, ClientStatus, Delivery, Direction
from sim.domain.voice import GEMINI_VOICES
from sim.ports.live_voice import (
    AUDIO,
    INTERRUPTED,
    TURN_COMPLETE,
    LiveVoiceError,
    LiveVoicePort,
    LiveVoiceSession,
    LiveVoiceSummary,
)
from sim.services.call_prompt import greeting
from sim.services.call_trace import CallTrace
from sim.services.conversation import ConversationService
from sim.services.dialog_state import apply_message
from sim.services.followup import FollowupService

log = logging.getLogger(__name__)

CALL_KIND = "call"            # Message.media_kind of the call's header line
CALL_WORKFLOW = "call"
UPLINK_MS = 40                # caller audio is sent to the Broker in 40 ms chunks
DRAIN_LIMIT_S = 10.0          # the model's last words still play after the Broker ended
MAX_LAG_S = 0.1               # a pacer later than this restarts its schedule instead of bursting
LANGUAGE_CODES = {"ru": "ru-RU", "uk": "uk-UA", "ua": "uk-UA", "en": "en-US"}

Sleep = Callable[[float], Awaitable[None]]


def language_code(language: str) -> str | None:
    """BCP-47 for the voice model when the persona's language is a plain code; else let the
    model detect it."""
    return LANGUAGE_CODES.get(language.strip().lower())


def clock_text(seconds: float | None) -> str:
    total = max(0, round(seconds or 0))
    return f"{total // 60}:{total % 60:02d}"


@dataclass(frozen=True)
class CallPlan:
    dialog_id: int
    voice: str
    system: str
    language: str | None
    greeting: str                # the typed first turn: she introduces herself as an AI assistant


@dataclass(frozen=True)
class CallOutcome:
    status: str                  # answered | skipped:<reason> | refused | failed
    summary: LiveVoiceSummary | None = None
    message_ids: list[int] = field(default_factory=list)


class CallService:
    def __init__(  # noqa: PLR0913
        self,
        factory: async_sessionmaker[AsyncSession],
        live: LiveVoicePort | None,
        conversation: Callable[[AsyncSession], ConversationService],
        *,
        max_seconds: float = 540.0,
        diagnostics: bool = False,
        clock: Clock = utcnow,
        sleep: Sleep = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.factory = factory
        self.live = live
        self.conversation = conversation
        self.max_seconds = max_seconds
        self.diagnostics = diagnostics
        self.clock = clock
        self.sleep = sleep
        self.mono = monotonic

    # ---- the call ------------------------------------------------------------------------
    async def handle(self, channel_id: int, call: IncomingCall) -> CallOutcome:
        trace = CallTrace(self.diagnostics, channel_id, self.mono)
        trace.event("ringing")
        plan = await self.prepare(channel_id, call.external_thread_id)
        if isinstance(plan, str):
            log.info("call on channel %s not answered: %s", channel_id, plan)
            return CallOutcome(f"skipped:{plan}")
        trace.event("plan_ready", dialog=plan.dialog_id, voice=plan.voice,
                    prompt_chars=len(plan.system))
        assert self.live is not None
        try:
            session = await self.live.open(
                voice=plan.voice, system=plan.system, language=plan.language,
                greeting=plan.greeting, workflow=CALL_WORKFLOW, max_seconds=self.max_seconds,
            )
        except LiveVoiceError as exc:
            log.warning("call on channel %s: broker refused: %s", channel_id, exc)
            return CallOutcome("refused")
        trace.event("session_opened", input_hz=session.input_rate_hz,
                    output_hz=session.output_rate_hz)
        started = self.clock()
        trace.event("answer_sent")
        try:
            await call.answer()
        except Exception as exc:
            log.warning("call on channel %s: answer failed: %s: %s", channel_id,
                        type(exc).__name__, str(exc)[:200])
            await session.finish()
            return CallOutcome("failed")
        trace.event("answered")
        try:
            await self.converse(call, session, trace)
        finally:
            summary = await session.finish()
            await call.hangup()
        trace.event("ended", end_reason=summary.end_reason, duration_s=summary.duration_s,
                    turns=len(summary.transcript))
        trace.summary()
        if not trace.counters["frames_out"]:
            log.warning("call on channel %s: the caller never heard her (model audio %s bytes, "
                        "caller frames %s)", channel_id, trace.counters["model_audio_bytes"],
                        trace.counters["frames_in"])
        ids = await self.record(plan.dialog_id, summary, started)
        return CallOutcome("answered", summary, ids)

    async def prepare(self, channel_id: int, thread_id: str) -> CallPlan | str:
        """Everything decided before picking up; a str is the reason not to answer."""
        if self.live is None:
            return "no_live_voice"
        async with self.factory() as s:
            channel = await ChannelRepo(s).get(channel_id)
            if channel is None or channel.is_archived:
                return "no_channel"
            base = await PersonaRepo(s).get(channel.persona_id)
            if base is None or not base.is_active:
                return "calls_off"
            conversation = self.conversation(s)
            dialog = await DialogRepo(s).by_thread(channel_id, thread_id)
            # In a running role game the game persona takes the owner's calls and nobody else's.
            persona = base if dialog is None else await conversation.game.caller(dialog, base)
            if persona is None:
                return "not_owner"
            if not persona.accept_calls:
                return "calls_off"
            if persona.voice_id not in GEMINI_VOICES:
                return "no_voice"
            if dialog is None:
                return "unknown_caller"
            if dialog.ai_paused or dialog.client.status is ClientStatus.REFUSED:
                return "operator_dialog"
            system = await conversation.call_prompt(dialog, persona)
            return CallPlan(dialog.id, persona.voice_id, system,
                            language_code(persona.language), greeting(persona.name))

    async def converse(
        self, call: IncomingCall, session: LiveVoiceSession, trace: CallTrace,
    ) -> None:
        """Until the caller hangs up, the Broker ends the conversation (time limit) or one of
        the three tasks fails."""
        out = PcmBuffer(frame_bytes(call.rate_hz))
        tasks = {
            "uplink": asyncio.ensure_future(self.uplink(call, session, trace)),
            "downlink": asyncio.ensure_future(self.downlink(session, out, call.rate_hz, trace)),
            "pacer": asyncio.ensure_future(self.pace(call, out, trace)),
        }
        try:
            done, _ = await asyncio.wait(tasks.values(), timeout=self.max_seconds + 30,
                                         return_when=asyncio.FIRST_COMPLETED)
            stopped = [name for name, task in tasks.items() if task in done]
            trace.event("conversation_over", by=",".join(stopped) or "time_limit")
            if stopped == ["downlink"] and tasks["downlink"].exception() is None:
                await self.drain(out)          # her last words before hanging up
        finally:
            for task in tasks.values():
                task.cancel()
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            for name, result in zip(tasks, results, strict=True):
                if isinstance(result, Exception):
                    log.warning("call on channel %s: %s stopped: %s: %s", trace.channel_id,
                                name, type(result).__name__, str(result)[:200])

    async def uplink(
        self, call: IncomingCall, session: LiveVoiceSession, trace: CallTrace,
    ) -> None:
        chunk = frame_bytes(session.input_rate_hz, UPLINK_MS)
        pending = bytearray()
        async for frame in call.frames():
            trace.first("first_caller_audio", bytes=len(frame))
            trace.count("frames_in")
            pending.extend(resample(frame, call.rate_hz, session.input_rate_hz))
            if len(pending) >= chunk:
                await session.send_audio(bytes(pending))
                trace.count("uplink_chunks")
                pending.clear()

    @staticmethod
    async def downlink(
        session: LiveVoiceSession, out: PcmBuffer, rate_hz: int, trace: CallTrace,
    ) -> None:
        async for event in session.events():
            if event.kind == AUDIO:
                trace.first("first_model_audio", bytes=len(event.pcm))
                trace.count("model_audio_bytes", len(event.pcm))
                out.push(resample(event.pcm, session.output_rate_hz, rate_hz))
            elif event.kind == INTERRUPTED:
                trace.event("interrupted", dropped_bytes=len(out))
                trace.count("interruptions")
                out.clear()                     # the caller talks: stop her mid-sentence
            elif event.kind == TURN_COMPLETE:
                trace.count("model_turns")
                tail = out.flush_tail()         # her last partial frame, padded with silence
                if tail:
                    out.push(tail)

    async def pace(self, call: IncomingCall, out: PcmBuffer, trace: CallTrace) -> None:
        """One frame every 10 ms while she speaks, idle while she is silent. The schedule is
        absolute, so a late wake-up is made up by a shorter next sleep and her speech does not
        fall behind; after a stall longer than MAX_LAG_S it restarts instead of sending the
        missed frames in one burst."""
        step = FRAME_MS / 1000
        due = self.mono()
        while True:
            frame = out.pop_frame()
            now = self.mono()
            if frame is None:
                due = now + step
                await self.sleep(step)
                continue
            if now - due > MAX_LAG_S:
                trace.count("pacer_restarts")
                due = now
            await call.play(frame)
            trace.first("first_frame_sent", bytes=len(frame))
            trace.count("frames_out")
            due += step
            await self.sleep(max(0.0, due - self.mono()))

    async def drain(self, out: PcmBuffer) -> None:
        deadline = self.mono() + DRAIN_LIMIT_S
        while len(out) and self.mono() < deadline:
            await self.sleep(FRAME_MS / 1000)

    # ---- after the call ------------------------------------------------------------------
    async def record(
        self, dialog_id: int, summary: LiveVoiceSummary, started: datetime
    ) -> list[int]:
        """The call in the dialog: a header line (the client called, how long) and then every
        recognised turn, so the admin reads the call and the next chat reply remembers it."""
        async with transaction(self.factory) as s:
            dialog = await DialogRepo(s).get(dialog_id)
            if dialog is None:
                return []
            await lock(s, NS_DIALOG, dialog.id)
            header = Message(
                dialog_id=dialog.id, direction=Direction.IN, author=Author.CLIENT,
                text=f"Входящий звонок · {clock_text(summary.duration_s)}", media_kind=CALL_KIND,
                delivery=Delivery.SENT, created_at=started,
                model=summary.model, cost_usd=summary.cost_usd,
                tokens_in=summary.tokens_in, tokens_out=summary.tokens_out,
            )
            rows = [header]
            for n, (role, text) in enumerate(summary.transcript, start=1):
                from_client = role == "user"
                rows.append(Message(
                    dialog_id=dialog.id, direction=Direction.IN if from_client else Direction.OUT,
                    author=Author.CLIENT if from_client else Author.AI, text=text.strip(),
                    delivery=Delivery.SENT, created_at=started + timedelta(microseconds=n),
                ))
            s.add_all(rows)
            await s.flush()
            apply_message(dialog, rows[-1])
            dialog.followup_step = 0
            client = dialog.client
            client.last_contact_at = max(started, client.last_contact_at or started)
            dialog.channel.last_activity_at = self.clock()
            await FollowupService(s, clock=self.clock).cancel(dialog.id)
            return [m.id for m in rows]
