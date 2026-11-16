"""Persona phone calls: an incoming Telegram call answered by the persona's live voice.

  call rings → prepare (who calls, may she answer, her call prompt) → let it ring a few seconds,
  as a person reaching for the phone would → open the Broker live
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
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.adapters.call_ambience import CallAmbience, prepare_call_ambience
from sim.adapters.channels.capabilities import IncomingCall
from sim.adapters.db.locks import NS_DIALOG, lock
from sim.adapters.db.models import Message
from sim.adapters.db.repositories.catalog import ChannelRepo, PersonaRepo
from sim.adapters.db.repositories.conversations import DialogRepo
from sim.adapters.db.session import transaction
from sim.adapters.pcm import FRAME_MS, PcmBuffer, frame_bytes, resample
from sim.clock import Clock, utcnow
from sim.domain.enums import Author, Delivery, Direction, VoiceAmbience
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
from sim.ports.llm import LLMResult
from sim.services.call_playback import CallContextChanged, CallPlayback, PlaybackTurn
from sim.services.call_prompt import greeting
from sim.services.call_trace import CallTrace
from sim.services.conversation import ConversationService, history_turns, with_persona_facts
from sim.services.dialog_state import apply_message
from sim.services.followup import FollowupService
from sim.services.guard import BLOCKED, GuardOutcome
from sim.services.journal import CALL_ISSUE, SYSTEM, CommunicationJournal
from sim.services.persona_ambience import contextual_ambience
from sim.services.prompt import Decision, Turn
from sim.services.reengagement import eligible, signature

log = logging.getLogger(__name__)

CALL_KIND = "call"            # Message.media_kind of the call's header line
CALL_WORKFLOW = "call"
UPLINK_MS = 40                # caller audio is sent to the Broker in 40 ms chunks
DRAIN_LIMIT_S = 10.0          # the model's last words still play after the Broker ended
MAX_LAG_S = 0.1               # a pacer later than this restarts its schedule instead of bursting
LANGUAGE_CODES = {
    "ru": "ru-RU", "uk": "uk-UA", "ua": "uk-UA", "en": "en-US",
    "русский": "ru-RU", "russian": "ru-RU",
    "украинский": "uk-UA", "українська": "uk-UA", "ukrainian": "uk-UA",
    "английский": "en-US", "english": "en-US",
}

Sleep = Callable[[float], Awaitable[None]]


def language_code(language: str) -> str | None:
    """BCP-47 for the voice model from the persona's language: a plain code (`ru`) or a phrase
    that starts with the language's name (`русский (переходит на украинский…)`). Unknown: the
    model detects it itself, and on a short "алло" it may well pick a wrong one."""
    words = re.findall(r"[^\W\d_]+", language.lower())
    return LANGUAGE_CODES.get(words[0]) if words else None


def clock_text(seconds: float | None) -> str:
    total = max(0, round(seconds or 0))
    return f"{total // 60}:{total % 60:02d}"


@dataclass(frozen=True)
class CallPlan:
    dialog_id: int
    voice: str
    system: str
    language: str | None
    greeting: str                # the typed first turn requests only «Алло»
    ambience: str = VoiceAmbience.OFF


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
        ring_s: tuple[float, float] = (0.0, 0.0),
        diagnostics: bool = False,
        clock: Clock = utcnow,
        sleep: Sleep = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.factory = factory
        self.live = live
        self.conversation = conversation
        self.max_seconds = max_seconds
        low, high = sorted(max(0.0, float(value)) for value in ring_s)
        self.ring_s = (low, high)
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
        await self.sleep(random.uniform(*self.ring_s))  # noqa: S311 - a human pause, not a secret
        trace.event("rang")
        if call.ended:
            log.info("call on channel %s not answered: hung up while ringing", channel_id)
            return CallOutcome("skipped:hung_up")
        # Local optional bed before paid live session; failure keeps clean audio.
        ambience = await prepare_call_ambience(plan.ambience, call.rate_hz)
        if plan.ambience != VoiceAmbience.OFF:
            trace.event("ambience", requested=plan.ambience, active=ambience is not None)
        if call.ended:
            return CallOutcome("skipped:hung_up")
        assert self.live is not None
        try:
            session = await self.live.open(
                voice=plan.voice, system=plan.system, language=plan.language,
                greeting=plan.greeting, workflow=CALL_WORKFLOW, max_seconds=self.max_seconds,
            )
        except LiveVoiceError as exc:
            log.warning("call on channel %s: broker refused: %s", channel_id, exc)
            await self.record_issue(plan.dialog_id, "provider_open_failed")
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
            await self.record_issue(plan.dialog_id, "answer_failed")
            return CallOutcome("failed")
        trace.event("answered")
        async def review(history: list[Turn], draft: str) -> GuardOutcome:
            return await self.review_turn(plan.dialog_id, history, draft, getattr(session, "model", None))

        playback = CallPlayback(review, clock=self.clock)
        try:
            await self.converse(call, session, trace, ambience, playback=playback)
        finally:
            try:
                summary = await session.finish()
            except Exception:
                log.exception("call summary failed dialog=%s", plan.dialog_id)
                playback.failure = playback.failure or "provider_finish_failed"
                summary = LiveVoiceSummary(duration_s=(self.clock() - started).total_seconds(),
                                           end_reason="provider_finish_failed")
            try:
                await call.hangup()
            except Exception:
                log.exception("call hangup failed dialog=%s", plan.dialog_id)
                playback.failure = playback.failure or "transport_hangup_failed"
        trace.event("ended", end_reason=summary.end_reason, duration_s=summary.duration_s,
                    turns=len(summary.transcript))
        trace.summary()
        if not trace.counters["frames_out"]:
            log.warning("call on channel %s: no speech frames accepted by transport (model audio %s bytes, "
                        "caller frames %s)", channel_id, trace.counters["model_audio_bytes"],
                        trace.counters["frames_in"])
        ids = await self.record(plan.dialog_id, summary, started, turns=playback.turns,
                                failure=playback.failure)
        return CallOutcome("answered", summary, ids)

    async def review_turn(self, dialog_id: int, spoken: list[Turn], draft: str,
                          model: str | None) -> GuardOutcome:
        """Fresh server facts and live turns for every audio release; no summary/model refresh."""
        async with self.factory() as s:
            conv = self.conversation(s)
            dialog = await conv.dialogs.get(dialog_id)
            if dialog is None or not eligible(dialog):
                return GuardOutcome(BLOCKED, draft, ["Диалог остановлен"])
            persona = await conv.personas.get(dialog.client.persona_id)
            if persona is None or not persona.is_active or not persona.accept_calls:
                return GuardOutcome(BLOCKED, draft, ["Звонки отключены"])
            revision, sig = dialog.activity_revision, signature(dialog, persona)
            recent, ctx = await conv.memory(dialog_id, await conv.dialog_context(dialog, persona),
                                            refresh_summary=False)
            ctx = with_persona_facts(persona, ctx)
            verdict = await conv.review(await conv.strategy_for(persona.id),
                [*history_turns(recent), *spoken], Decision(draft), LLMResult(text=draft, model=model),
                ctx, dialog_id, guard_workflow="call_guard", medium="voice")
            dialog = await conv.reload(dialog_id)
            await s.refresh(persona)
            if not eligible(dialog) or not persona.is_active or not persona.accept_calls:
                return GuardOutcome(BLOCKED, draft, ["Ответ отменён: ИИ или звонки выключены."], verdict.model)
            if dialog.activity_revision != revision or signature(dialog, persona) != sig:
                log.info("voice candidate cancelled dialog=%s revision_before=%s revision_after=%s signature_changed=%s",
                         dialog_id, revision, dialog.activity_revision, signature(dialog, persona) != sig)
                raise CallContextChanged()
            return verdict

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
            if not eligible(dialog):
                return "operator_dialog"
            system = await self.conversation(s).call_prompt(dialog, persona)
            # Fixed/random note presets do not change previously clean calls.
            ambience = (contextual_ambience(persona.routine, persona.timezone, self.clock())
                        if persona.voice_ambience == VoiceAmbience.CONTEXTUAL
                        else VoiceAmbience.OFF)
            return CallPlan(dialog.id, persona.voice_id, system,
                            language_code(persona.language), greeting(persona.name), ambience)

    async def converse(
        self, call: IncomingCall, session: LiveVoiceSession, trace: CallTrace,
        ambience: CallAmbience | None = None,
        *, playback: CallPlayback | None = None,
    ) -> None:
        """Until the caller hangs up, the Broker ends the conversation (time limit) or one of
        the three tasks fails."""
        out = PcmBuffer(frame_bytes(call.rate_hz))
        tasks = {
            "uplink": asyncio.ensure_future(self.uplink(call, session, trace)),
            "downlink": asyncio.ensure_future(
                playback.run(session, out, call.rate_hz, trace) if playback is not None
                else self.downlink(session, out, call.rate_hz, trace)),
            "pacer": asyncio.ensure_future(self.pace(call, out, trace, ambience, playback=playback)),
        }
        try:
            done, _ = await asyncio.wait(tasks.values(), timeout=self.max_seconds + 30,
                                         return_when=asyncio.FIRST_COMPLETED)
            stopped = [name for name, task in tasks.items() if task in done]
            if not stopped and playback is not None:
                playback.failure = playback.failure or "call_timeout"
            trace.event("conversation_over", by=",".join(stopped) or "time_limit")
            if stopped == ["downlink"] and tasks["downlink"].exception() is None:
                await self.drain(out)          # her last words before hanging up
                if playback is not None:
                    try:
                        async with asyncio.timeout(DRAIN_LIMIT_S):
                            await playback.drained.wait()
                    except TimeoutError:
                        trace.event("playback_drain_timeout")
                        playback.failure = playback.failure or "playback_drain_timeout"
        finally:
            for task in tasks.values():
                task.cancel()
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            for name, result in zip(tasks, results, strict=True):
                if isinstance(result, Exception):
                    if playback is not None:
                        playback.failure = playback.failure or f"{name}_failed"
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

    async def pace(
        self, call: IncomingCall, out: PcmBuffer, trace: CallTrace,
        ambience: CallAmbience | None = None,
        *, playback: CallPlayback | None = None,
    ) -> None:
        """One frame every 10 ms; a selected bed continues in pauses (not in speech buffer).
        Without a bed we stay idle while she is silent. The schedule is
        absolute, so a late wake-up is made up by a shorter next sleep and her speech does not
        fall behind; after a stall longer than MAX_LAG_S it restarts instead of sending the
        missed frames in one burst."""
        step = FRAME_MS / 1000
        due = self.mono()
        while True:
            frame = out.pop_frame()
            turn = playback.playing if playback is not None and frame is not None else None
            now = self.mono()
            speech = frame is not None
            if ambience is not None:
                frame = ambience.mix(frame)
            if frame is None:
                due = now + step
                await self.sleep(step)
                continue
            if now - due > MAX_LAG_S:
                trace.count("pacer_restarts")
                due = now
            await call.play(frame)
            if turn is not None:
                playback.acknowledge(turn, len(frame))
            if speech:
                trace.first("first_frame_sent", bytes=len(frame))
                trace.count("frames_out")
            else:
                trace.count("ambience_frames_out")
            due += step
            await self.sleep(max(0.0, due - self.mono()))

    async def drain(self, out: PcmBuffer) -> None:
        deadline = self.mono() + DRAIN_LIMIT_S
        while len(out) and self.mono() < deadline:
            await self.sleep(FRAME_MS / 1000)

    # ---- after the call ------------------------------------------------------------------
    async def record(
        self, dialog_id: int, summary: LiveVoiceSummary, started: datetime,
        *, turns: list[PlaybackTurn] | None = None, failure: str = "",
    ) -> list[int]:
        """The call in the dialog: a header line (the client called, how long) and then every
        recognised turn, so the admin reads the call and the next chat reply remembers it."""
        async with transaction(self.factory) as s:
            dialog = await DialogRepo(s).get(dialog_id)
            if dialog is None:
                return []
            await lock(s, NS_DIALOG, dialog.id)
            await s.refresh(dialog)
            header = Message(
                dialog_id=dialog.id, direction=Direction.IN, author=Author.CLIENT,
                text=f"Входящий звонок · {clock_text(summary.duration_s)}", media_kind=CALL_KIND,
                delivery=Delivery.SENT, created_at=started,
                model=summary.model, cost_usd=summary.cost_usd,
                provider=summary.provider,
                tokens_in=summary.tokens_in, tokens_out=summary.tokens_out,
            )
            rows = [header]
            recorded = turns if turns is not None else [PlaybackTurn(role, text, released=True)
                                                        for role, text in summary.transcript]
            for n, turn in enumerate(recorded, start=1):
                from_client = turn.role == "user"
                guard = turn.guard
                rows.append(Message(
                    dialog_id=dialog.id, direction=Direction.IN if from_client else Direction.OUT,
                    author=Author.CLIENT if from_client else Author.AI, text=turn.text.strip(),
                    model=summary.model if not from_client else None,
                    provider=summary.provider if not from_client else None,
                    delivery=Delivery.SENT if turn.delivered else Delivery.CANCELLED,
                    error=(turn.cancel_reason or (f"Voice playback incomplete: {turn.played_bytes}/{turn.queued_bytes} PCM bytes accepted"
                           if turn.released and not turn.delivered else None)),
                    guard_verdict=guard.verdict if guard else None,
                    guard_violations=guard.violations if guard else None,
                    guard_model=guard.model if guard else None,
                    created_at=started + timedelta(microseconds=n),
                ))
            s.add_all(rows)
            await s.flush()
            delivered = [row for row in rows if row.delivery is Delivery.SENT]
            apply_message(dialog, delivered[-1])
            dialog.last_message_at = max(started, self.clock())
            dialog.followup_step = 0
            client = dialog.client
            client.last_contact_at = max(started, client.last_contact_at or started)
            dialog.channel.last_activity_at = self.clock()
            await FollowupService(s, clock=self.clock).cancel(dialog.id)
            if failure or any(t.cancel_reason for t in recorded):
                header.error = failure or "context_changed"
                await CommunicationJournal(s, clock=self.clock).record(
                    dialog.id, CALL_ISSUE, new="Звонок завершён с технической проблемой",
                    who=SYSTEM, reason="Часть ответа могла не прозвучать. Можно продолжить в чате.")
            return [m.id for m in rows]

    async def record_issue(self, dialog_id: int, code: str) -> None:
        """A failed connection is context, never permission to change the owner's AI policy."""
        log.info("call issue dialog=%s code=%s", dialog_id, code)
        async with transaction(self.factory) as s:
            await CommunicationJournal(s, clock=self.clock).record(
                dialog_id, CALL_ISSUE, new="Не удалось начать голосовой разговор",
                reason="Ответ не прозвучал. Можно продолжить в чате.")
