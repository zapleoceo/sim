"""Persona phone calls: an incoming Telegram call answered by the persona's live voice.

  call rings → prepare (who calls, may she answer, her call prompt) → open the Broker live
  session (Gemini Live) → answer → caller audio up, model speech down, paced at real time →
  hang up → the transcript lands in the dialog.

She answers only a client she already chats with (an existing dialog of that channel), whose
dialog is not taken over by an operator, and only when her `accept_calls` is on and she has a
voice. Otherwise the call is left alone and keeps ringing for a human on the account's devices.
The Broker session is opened BEFORE answering: when the Broker refuses, nobody picked up.

No database transaction is open while the call runs; the transcript is written afterwards in a
short one, like any inbound message."""
from __future__ import annotations

import asyncio
import contextlib
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
from sim.services.call_prompt import GREETING
from sim.services.conversation import ConversationService
from sim.services.dialog_state import apply_message
from sim.services.followup import FollowupService

log = logging.getLogger(__name__)

CALL_KIND = "call"            # Message.media_kind of the call's header line
CALL_WORKFLOW = "call"
UPLINK_MS = 40                # caller audio is sent to the Broker in 40 ms chunks
DRAIN_LIMIT_S = 10.0          # the model's last words still play after the Broker ended
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
        clock: Clock = utcnow,
        sleep: Sleep = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.factory = factory
        self.live = live
        self.conversation = conversation
        self.max_seconds = max_seconds
        self.clock = clock
        self.sleep = sleep
        self.mono = monotonic

    # ---- the call ------------------------------------------------------------------------
    async def handle(self, channel_id: int, call: IncomingCall) -> CallOutcome:
        plan = await self.prepare(channel_id, call.external_thread_id)
        if isinstance(plan, str):
            log.info("call on channel %s not answered: %s", channel_id, plan)
            return CallOutcome(f"skipped:{plan}")
        assert self.live is not None
        try:
            session = await self.live.open(
                voice=plan.voice, system=plan.system, language=plan.language,
                greeting=GREETING, workflow=CALL_WORKFLOW, max_seconds=self.max_seconds,
            )
        except LiveVoiceError as exc:
            log.warning("call on channel %s: broker refused: %s", channel_id, exc)
            return CallOutcome("refused")
        started = self.clock()
        try:
            await call.answer()
        except Exception as exc:
            log.warning("call on channel %s: answer failed: %s", channel_id, type(exc).__name__)
            await session.finish()
            return CallOutcome("failed")
        try:
            await self.converse(call, session)
        finally:
            summary = await session.finish()
            await call.hangup()
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
            persona = await PersonaRepo(s).get(channel.persona_id)
            if persona is None or not persona.is_active or not persona.accept_calls:
                return "calls_off"
            if persona.voice_id not in GEMINI_VOICES:
                return "no_voice"
            dialog = await DialogRepo(s).by_thread(channel_id, thread_id)
            if dialog is None:
                return "unknown_caller"
            if dialog.ai_paused or dialog.client.status is ClientStatus.REFUSED:
                return "operator_dialog"
            system = await self.conversation(s).call_prompt(dialog, persona)
            return CallPlan(dialog.id, persona.voice_id, system, language_code(persona.language))

    async def converse(self, call: IncomingCall, session: LiveVoiceSession) -> None:
        """Until the caller hangs up or the Broker ends the conversation (time limit)."""
        out = PcmBuffer(frame_bytes(call.rate_hz))
        uplink = asyncio.ensure_future(self.uplink(call, session))
        downlink = asyncio.ensure_future(self.downlink(session, out, call.rate_hz))
        pacer = asyncio.ensure_future(self.pace(call, out))
        try:
            done, _ = await asyncio.wait({uplink, downlink}, timeout=self.max_seconds + 30,
                                         return_when=asyncio.FIRST_COMPLETED)
            if downlink in done and uplink not in done:
                await self.drain(out)          # her last words before hanging up
        finally:
            for task in (uplink, downlink, pacer):
                task.cancel()
            for task in (uplink, downlink, pacer):
                with contextlib.suppress(BaseException):
                    await task

    async def uplink(self, call: IncomingCall, session: LiveVoiceSession) -> None:
        chunk = frame_bytes(session.input_rate_hz, UPLINK_MS)
        pending = bytearray()
        async for frame in call.frames():
            pending.extend(resample(frame, call.rate_hz, session.input_rate_hz))
            if len(pending) >= chunk:
                await session.send_audio(bytes(pending))
                pending.clear()

    @staticmethod
    async def downlink(session: LiveVoiceSession, out: PcmBuffer, rate_hz: int) -> None:
        async for event in session.events():
            if event.kind == AUDIO:
                out.push(resample(event.pcm, session.output_rate_hz, rate_hz))
            elif event.kind == INTERRUPTED:
                out.clear()                     # the caller talks: stop her mid-sentence
            elif event.kind == TURN_COMPLETE:
                tail = out.flush_tail()         # her last partial frame, padded with silence
                if tail:
                    out.push(tail)

    async def pace(self, call: IncomingCall, out: PcmBuffer) -> None:
        """One frame every 10 ms, on a drift-free schedule; idle while she is silent."""
        step = FRAME_MS / 1000
        due = self.mono()
        while True:
            frame = out.pop_frame()
            now = self.mono()
            if frame is None:
                due = now + step
                await self.sleep(step)
                continue
            await call.play(frame)
            due = max(due + step, now)
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
