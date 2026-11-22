"""Incoming private calls answered by the persona's live voice (docs/calls.md).

  ring -> resolve the chat -> may she answer? (the AI gate, sim.domain.ai_gate, then her
          calls switch and the call model; no: decline, recorded)
       -> is she free? (her availability after the attention settings, docs/attention.md:
          away — it rings unanswered, missed; busy — she may decline; either way the `call`
          event carries `life_busy` and her phone check decides when she texts back)
       -> wait a human ring delay -> open the Broker live session (before picking up: when the
          Broker refuses, nobody picked up) -> answer -> talk (AudioBridge) -> hang up
       -> Call row + `call` event in one transaction (the passport learns it at night).

Her own call (`dial`, a due planned contact, docs/initiative.md):

  contact -> may she call? (the same AI gate and switches; the chat's messenger must be
          Telegram) -> `call` row (direction out) -> ring the client (`RING_OUT_S`)
       -> picked up: open the live session with the reason of the call (she waits for his
          «алло») -> talk -> hang up; nobody picked up / declined / busy: a missed call, and
          an `initiative` event makes her text him why she called
       -> the contact closes with the outcome in the same transaction as the call.

Every outcome is a `call` row and a `call` event: declined, missed (the caller hung up first),
failed (Broker or Telegram refused), ended (a conversation took place). No database transaction
is open while the call runs; nothing slow except the live session runs in this process."""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.channels.delivery import peer_of
from sim.config import Settings
from sim.db.models import Call, Chat, Client, Persona, PlannedContact
from sim.db.session import transaction
from sim.domain.agents import AgentKey, agent_config
from sim.domain.ai_gate import AiBlock, ai_block
from sim.domain.attention import attention_settings
from sim.domain.enums import (
    CallDirection,
    CallStatus,
    ContactSource,
    ContactStatus,
    EventKind,
)
from sim.domain.initiative import NO_ANSWER_RESULTS, NO_ANSWER_TOPIC, RING_OUT_S
from sim.domain.life import BusyLevel
from sim.events import append_event
from sim.ports import (
    ClockPort,
    LivePort,
    LiveSession,
    MediaStoragePort,
    MediaToolsPort,
    Peer,
    PortError,
)
from sim.services.attention.schedule import check_after_notice, queue_check
from sim.services.attention.timing import Availability, availability
from sim.services.initiative import (
    PERSONA_ACTOR,
    calls_unavailable,
    channel_kind_of,
    finish_contact,
    write_first,
)
from sim.services.life.store import load_life
from sim.services.recordings import RECORDING_MIME, RECORDING_PREFIX
from sim.services.turn_state import AFTER_CALL_KEY, LIFE_ACTIVITY_KEY, LIFE_REFUSED_KEY
from sim.telegram.calls.backend import (
    CallAudio,
    CallBackend,
    CallGone,
    IncomingCall,
    NoAnswer,
    OutgoingCall,
)
from sim.telegram.calls.bridge import AudioBridge, Outcome
from sim.telegram.calls.recording import CallRecorder
from sim.telegram.calls.tempo import TempoFactory, ffmpeg_tempo

log = logging.getLogger(__name__)

ACTORS = {CallDirection.IN: "client", CallDirection.OUT: PERSONA_ACTOR}   # who called
NO_ANSWER_TEXT_S = (60.0, 240.0)       # after a missed call of hers she texts within minutes
AFTER_CALL_TEXT_S = (15.0, 45.0)       # after a talk she does what she promised on it
OPEN_STATUSES = (CallStatus.RINGING, CallStatus.ACTIVE)
PARTIAL_DECLINE_SHARE = 0.5            # busy: she declines about every second call
MISSED_RING_S = 45.0                   # away: it rings this long, then the caller gets nothing
RING_POLL_S = 1.0


@dataclass(frozen=True)
class Refusal:
    status: CallStatus                 # MISSED (away) or DECLINED (busy)
    busy: BusyLevel
    activity: str


class ChatResolver(Protocol):
    async def resolve_chat(self, session: AsyncSession, channel_id: int, peer: Peer) -> Chat: ...


class CallContextSource(Protocol):
    async def for_call(self, session: AsyncSession, chat_id: int) -> Any: ...


class CallTasks(Protocol):
    async def refresh_memory(self, chat_id: int) -> Any: ...

    async def phone_check(self, persona_id: int, delay_s: float = 0) -> Any: ...

    async def reply(self, chat_id: int, delay_s: float = 0) -> Any: ...


def life_refusal(av: Availability, uniform: Callable[[float, float], float]) -> Refusal | None:
    """Decided at ring time from her availability after the attention settings
    (docs/attention.md): away (asleep, running) — she does not pick up, a missed call; busy —
    she may decline (`PARTIAL_DECLINE_SHARE`); free — she answers after the ring delay."""
    if av.level == BusyLevel.AWAY:
        return Refusal(CallStatus.MISSED, av.level, av.activity)
    if av.level == BusyLevel.PARTIAL and uniform(0.0, 1.0) < PARTIAL_DECLINE_SHARE:
        return Refusal(CallStatus.DECLINED, av.level, av.activity)
    return None


PERSONA_OFF_REASON = "persona AI is off (or the persona is archived)"
# A call's `error` for each reason of the AI gate (sim.domain.ai_gate).
GATE_REASONS = {
    AiBlock.NO_PERSONA: PERSONA_OFF_REASON,
    AiBlock.ARCHIVED: PERSONA_OFF_REASON,
    AiBlock.PERSONA_OFF: PERSONA_OFF_REASON,
    AiBlock.CLIENT_OFF: "AI is off for this client",
    AiBlock.CHAT_OFF: "AI is off in this chat",
    AiBlock.NEEDS_OPERATOR: "the chat waits for an operator",
}


def refuse_reason(*, calls_enabled: bool, persona: Persona | None, client: Client | None,
                  chat: Chat) -> str | None:
    """Why she must not pick up, or None. Checked in this order, first reason wins: calls
    switched off for the process, the AI gate (the same rule as for messages), her calls, the
    call agent's model."""
    if not calls_enabled:
        return "calls are disabled (SIM_CALLS_ENABLED)"
    blocked = ai_block(persona, client, chat)
    if blocked is not None or persona is None:
        return GATE_REASONS[blocked or AiBlock.NO_PERSONA]
    if not persona.calls_enabled:
        return "persona calls are off"
    if not agent_config(persona, AgentKey.LIVE).model:
        return "no live model chosen for the persona"
    return None


def broker_transcript(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """The Broker's own transcript ([{role, text}]) when the live events carried none."""
    lines = []
    for turn in summary.get("transcript") or []:
        if not isinstance(turn, dict) or not str(turn.get("text") or "").strip():
            continue
        who = "client" if turn.get("role") in ("user", "client") else "persona"
        lines.append({"who": who, "text": " ".join(str(turn["text"]).split()),
                      "t": float(turn.get("t") or 0.0)})
    return lines


@dataclass(frozen=True)
class Ring:
    call_id: int
    chat_id: int
    status: CallStatus                 # RINGING = may answer; DECLINED / FAILED = recorded
    context: Any = None                # CallContext when RINGING
    life_busy: bool = False            # not taken because of her life: she writes later
    persona_id: int | None = None
    check_at: datetime | None = None   # her phone check this call asked for (life_busy)
    rang_at: datetime | None = None
    contact_id: int | None = None      # her call: the planned contact it carries out
    topic: str = ""


class CallService:
    def __init__(
        self, *, sessions: async_sessionmaker[AsyncSession], settings: Settings,
        clock: ClockPort, live: LivePort, inbox: ChatResolver, context: CallContextSource,
        tasks: CallTasks, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        uniform: Callable[[float, float], float] | None = None,
        tempo: TempoFactory | None = ffmpeg_tempo,
        tools: MediaToolsPort | None = None, storage: MediaStoragePort | None = None,
    ) -> None:
        self.sessions = sessions
        self.settings = settings
        self.clock = clock
        self.live = live
        self.inbox = inbox
        self.context = context
        self.tasks = tasks
        self.sleep = sleep
        self.mono = monotonic
        self.uniform = uniform or secrets.SystemRandom().uniform
        self.tempo = tempo
        self.tools = tools
        self.storage = storage

    # --- the call ------------------------------------------------------------------------

    async def handle(self, channel_id: int, call: IncomingCall) -> CallStatus:
        """Runs one incoming call to the end and returns its final status."""
        peer = await call.peer()
        ring = await self.ring(channel_id, peer, call.external_id)
        if ring.status != CallStatus.RINGING:
            if ring.status == CallStatus.MISSED:
                await self.let_ring(call)
            else:
                await call.decline()
            if ring.life_busy:
                await self.write_later(ring)
            return ring.status
        low, high = sorted((self.settings.call_ring_min_s, self.settings.call_ring_max_s))
        await self.sleep(max(0.0, self.uniform(low, high)))
        if call.ended:
            return await self.close(ring, CallStatus.MISSED, error="the caller hung up first")
        ctx = ring.context
        max_seconds = int(getattr(ctx, "max_seconds", 0) or self.settings.call_max_seconds)
        try:
            session = await self.live.open(
                model=ctx.model, system=ctx.system, voice=ctx.voice, language=ctx.language,
                greeting=ctx.greeting, max_seconds=max_seconds, key_tier=ctx.key_tier)
        except Exception as exc:
            await call.decline()
            return await self.close(ring, CallStatus.FAILED, error=f"live: {short(exc)}")
        try:
            await call.answer()
        except CallGone:
            await self.finish_live(session)
            return await self.close(ring, CallStatus.MISSED, error="the caller hung up first")
        except Exception as exc:
            await self.finish_live(session)
            return await self.close(ring, CallStatus.FAILED, error=f"answer: {short(exc)}")
        return await self.talk(ring, call, session, max_seconds)

    async def dial(self, contact_id: int, backends: Mapping[int, CallBackend]) -> CallStatus | None:
        """Her call of a started planned contact, to the end; None = it was not placed (the
        contact is closed with the reason)."""
        placed = await self.place(contact_id)
        if placed is None:
            return None
        ring, channel_id, peer = placed
        backend = backends.get(channel_id)
        if backend is None:
            return await self.close(ring, CallStatus.FAILED,
                                    error="calls of this account are not connected")
        call: OutgoingCall | None = None
        try:
            call = await backend.dial(peer)
            await call.ring(RING_OUT_S)
        except NoAnswer as exc:
            status = CallStatus.MISSED if exc.reason == "no_answer" else CallStatus.DECLINED
            return await self.close(ring, status, error=None, end_reason=exc.reason)
        except Exception as exc:
            if call is not None:
                await call.hangup()
            return await self.close(ring, CallStatus.FAILED, error=f"dial: {short(exc)}")
        ctx = ring.context
        max_seconds = int(getattr(ctx, "max_seconds", 0) or self.settings.call_max_seconds)
        try:
            session = await self.live.open(
                model=ctx.model, system=ctx.system, voice=ctx.voice, language=ctx.language,
                greeting=ctx.greeting, max_seconds=max_seconds, key_tier=ctx.key_tier)
        except Exception as exc:
            await call.hangup()
            return await self.close(ring, CallStatus.FAILED, error=f"live: {short(exc)}")
        return await self.talk(ring, call, session, max_seconds)

    async def place(self, contact_id: int) -> tuple[Ring, int, Peer] | None:
        """Checks and records her call (the `call` row, ringing) for a started contact:
        (ring, channel id, the client's peer), or None with the contact closed."""
        async with transaction(self.sessions) as session:
            contact = await session.get(PlannedContact, contact_id, with_for_update=True)
            if contact is None or contact.status != ContactStatus.STARTED:
                return None
            chat = await session.get(Chat, contact.chat_id)
            persona = await session.get(Persona, chat.persona_id) if chat else None
            client = await session.get(Client, chat.client_id) if chat else None
            reason = await self.dial_refusal(session, contact, chat, persona, client)
            peer: Peer | None = None
            if reason is None and chat is not None:
                try:
                    peer = await peer_of(session, chat)
                except PortError as exc:
                    reason = str(exc)
            if reason is not None or chat is None or persona is None or peer is None:
                finish_contact(contact, ContactStatus.FAILED,
                               f"не позвонила: {reason or 'чат пропал'}", self.clock.now())
                return None
            row = Call(chat_id=chat.id, direction=CallDirection.OUT, status=CallStatus.RINGING,
                       external_id=peer.external_id, transcript=[])
            session.add(row)
            await session.flush()
            contact.call_id = row.id
            try:
                context = await self.context.for_call(session, chat.id,
                                                      outgoing_topic=contact.topic)
            except Exception as exc:
                log.exception("call %s: context not built", row.id)
                finish_contact(contact, ContactStatus.FAILED, "не позвонила: контекст",
                               self.clock.now())
                await self.record(session, row, CallStatus.FAILED,
                                  error=f"context: {short(exc)}", ended_at=self.clock.now())
                return None
            ring = Ring(row.id, chat.id, CallStatus.RINGING, context, persona_id=persona.id,
                        contact_id=contact.id, topic=contact.topic)
            return ring, chat.channel_id, peer

    async def dial_refusal(self, session: AsyncSession, contact: PlannedContact,
                           chat: Chat | None, persona: Persona | None,
                           client: Client | None) -> str | None:
        """Why she must not call now, or None: the same gate as for her answers, the call
        switches and the messenger; her own idea also needs «Звонит сама» still on."""
        if chat is None or persona is None:
            return "чат пропал"
        blocked = ai_block(persona, client, chat)
        if blocked is not None:
            return GATE_REASONS[blocked]
        unavailable = calls_unavailable(persona, await channel_kind_of(session, chat),
                                        calls_enabled=self.settings.calls_enabled)
        if unavailable:
            return unavailable
        if contact.source == ContactSource.PERSONA and not chat.calls_out:
            return "«Звонит сама» выключили"
        busy = await session.scalar(select(Call.id).where(
            Call.chat_id == chat.id, Call.status.in_(OPEN_STATUSES)).limit(1))
        return "с ним уже идёт звонок" if busy is not None else None

    async def let_ring(self, call: IncomingCall) -> None:
        """Nobody picks up: it rings until the caller gives up (or `MISSED_RING_S`)."""
        waited = 0.0
        while not call.ended and waited < MISSED_RING_S:
            await self.sleep(RING_POLL_S)
            waited += RING_POLL_S
        if not call.ended:
            await call.decline()

    async def write_later(self, ring: Ring) -> None:
        """Her phone check sees the missed call and decides when she texts back
        (docs/attention.md); the reply turn counts the call as unanswered (turn_state)."""
        if ring.persona_id is None or ring.rang_at is None:
            return
        try:
            await queue_check(self.tasks, ring.persona_id, ring.check_at, ring.rang_at)
        except Exception:
            log.exception("call %s: phone check after the call not queued", ring.call_id)

    async def talk(self, ring: Ring, call: CallAudio, session: LiveSession,
                   max_seconds: int) -> CallStatus:
        started_at, started = self.clock.now(), self.mono()
        await self.mark_active(ring.call_id, started_at)
        recorder = (CallRecorder() if getattr(ring.context, "record", False)
                    and self.tools is not None and self.storage is not None else None)
        try:
            outcome = await AudioBridge(
                call, session, max_seconds=max_seconds, monotonic=self.mono,
                speed=float(getattr(ring.context, "speed", 1.0) or 1.0),
                tempo=self.tempo, recorder=recorder).run()
        except Exception as exc:          # never leave the row open
            outcome = Outcome("bridge_error", short(exc), [])
        finally:
            summary = await self.finish_live(session)
            await call.hangup()
        duration = max(0.0, self.mono() - started)
        if not outcome.frames_out:
            log.warning("call %s: the caller never heard her (%s)", ring.call_id, outcome.reason)
        cost = summary.get("cost_usd")
        transcript = outcome.transcript or broker_transcript(summary)
        she_spoke = any(line.get("who") == "persona" for line in transcript)
        status = await self.close(
            ring, CallStatus.ENDED, error=outcome.error, started_at=started_at,
            duration_s=round(duration, 1), transcript=transcript,
            cost_usd=float(cost) if isinstance(cost, int | float) else None,
            model=str(summary.get("model") or getattr(ring.context, "model", "") or ""),
            end_reason=outcome.reason, extra={AFTER_CALL_KEY: True} if she_spoke else None)
        try:
            # A call that made today too long for the context compresses its morning.
            await self.tasks.refresh_memory(ring.chat_id)
            if she_spoke:
                # She still holds the phone: what she promised on the call (a video note, a
                # text, a call back) she does now (docs/calls.md, «После звонка»).
                await self.tasks.reply(ring.chat_id, self.uniform(*AFTER_CALL_TEXT_S))
        except Exception:
            log.exception("call %s: the turns after the call not queued", ring.call_id)
        if recorder is not None:
            await self.save_recording(ring.call_id, recorder)
        return status

    async def save_recording(self, call_id: int, recorder: CallRecorder) -> None:
        """The talk as stereo OGG in the media storage, after the call is closed: a failure
        only loses the audio (logged), never the call (docs/calls.md, «Запись звонка»)."""
        assert self.tools is not None and self.storage is not None
        stereo = recorder.stereo()
        if not stereo:
            return
        try:
            ogg, seconds = await self.tools.call_recording(stereo, recorder.rate_hz)
            key = await self.storage.put(ogg, mime=RECORDING_MIME, prefix=RECORDING_PREFIX)
        except Exception:
            log.exception("call %s: the recording was not saved", call_id)
            return
        async with transaction(self.sessions) as session:
            row = await session.get(Call, call_id)
            if row is None:
                await self.storage.delete(key)
                return
            row.recording_key, row.recording_s = key, seconds

    # --- database ------------------------------------------------------------------------

    async def ring(self, channel_id: int, peer: Peer, external_id: str) -> Ring:
        """Records the ringing call and decides. A refused call is closed right here."""
        async with transaction(self.sessions) as session:
            chat = await self.inbox.resolve_chat(session, channel_id, peer)
            persona = await session.get(Persona, chat.persona_id)
            client = await session.get(Client, chat.client_id)
            reason = refuse_reason(calls_enabled=self.settings.calls_enabled, persona=persona,
                                   client=client, chat=chat)
            row = Call(chat_id=chat.id, direction=CallDirection.IN, status=CallStatus.RINGING,
                       external_id=external_id, transcript=[])
            session.add(row)
            await session.flush()
            if reason is None and persona is not None:
                refused = await self.refuse_by_life(session, row, persona, chat)
                if refused is not None:
                    return refused
            context = None
            if reason is None:
                try:
                    context = await self.context.for_call(session, chat.id)
                except Exception as exc:
                    log.exception("call %s: context not built", row.id)
                    reason = f"context: {short(exc)}"
                    status = CallStatus.FAILED
                else:
                    return Ring(row.id, chat.id, CallStatus.RINGING, context)
            else:
                status = CallStatus.DECLINED
            await self.record(session, row, status, error=reason, ended_at=self.clock.now())
            return Ring(row.id, chat.id, status)

    async def refuse_by_life(self, session: AsyncSession, row: Call, persona: Persona,
                             chat: Chat) -> Ring | None:
        """Her life may keep her from the call (docs/attention.md, «Звонки»). A refused call
        is closed here, and unless the owner turned it off («Писать после пропущенного
        звонка») it asks for her phone check: she texts back when she can."""
        now = self.clock.now()
        settings = attention_settings(persona.attention)
        av = availability(await load_life(session, persona, now), settings,
                          instant=chat.attention_instant)
        refusal = life_refusal(av, self.uniform)
        if refusal is None:
            return None
        await self.record(session, row, refusal.status,
                          error=f"{refusal.busy.value}: {refusal.activity}", ended_at=now,
                          extra={LIFE_REFUSED_KEY: refusal.busy.value,
                                 LIFE_ACTIVITY_KEY: refusal.activity})
        check_at = None
        if settings.after_missed_call:
            check_at = await check_after_notice(session, persona, now,
                                                self.uniform(0.0, 1.0), av=av)
        return Ring(row.id, chat.id, refusal.status, life_busy=True, persona_id=persona.id,
                    check_at=check_at, rang_at=now)

    async def mark_active(self, call_id: int, started_at: datetime) -> None:
        async with transaction(self.sessions) as session:
            row = await session.get(Call, call_id)
            if row is not None:
                row.status = CallStatus.ACTIVE
                row.started_at = started_at

    async def close(self, ring: Ring, status: CallStatus, *, error: str | None,
                    started_at: datetime | None = None, duration_s: float | None = None,
                    transcript: list[dict[str, Any]] | None = None,
                    cost_usd: float | None = None, model: str = "",
                    end_reason: str = "", extra: dict[str, Any] | None = None) -> CallStatus:
        ended_at = (started_at + timedelta(seconds=duration_s)
                    if started_at is not None and duration_s is not None else self.clock.now())
        texts_later = False
        async with transaction(self.sessions) as session:
            row = await session.get(Call, ring.call_id)
            if row is None:
                return status
            row.started_at = started_at or row.started_at
            row.duration_s = duration_s
            row.transcript = transcript or []
            row.cost_usd = cost_usd
            row.model = model
            await self.record(session, row, status, error=error, ended_at=ended_at,
                              end_reason=end_reason, extra=extra)
            if ring.contact_id is not None:
                texts_later = await self.close_contact(session, ring, status, error=error,
                                                       end_reason=end_reason,
                                                       duration_s=duration_s)
        if texts_later:
            try:
                await self.tasks.reply(ring.chat_id, self.uniform(*NO_ANSWER_TEXT_S))
            except Exception:
                log.exception("call %s: the text after the missed call not queued",
                              ring.call_id)
        return status

    async def close_contact(self, session: AsyncSession, ring: Ring, status: CallStatus, *,
                            error: str | None, end_reason: str,
                            duration_s: float | None) -> bool:
        """Closes the planned contact of her call. Nobody picked up -> an `initiative` event:
        she texts him why she called (True: the caller queues that turn)."""
        contact = await session.get(PlannedContact, ring.contact_id)
        if contact is None:
            return False
        now = self.clock.now()
        if status == CallStatus.ENDED:
            minutes = round((duration_s or 0.0) / 60, 1)
            finish_contact(contact, ContactStatus.DONE, f"поговорили {minutes} мин", now)
            return False
        if status in (CallStatus.MISSED, CallStatus.DECLINED):
            finish_contact(contact, ContactStatus.FAILED,
                           NO_ANSWER_RESULTS.get(end_reason, "не взял трубку"), now)
            await write_first(session, ring.chat_id, actor=PERSONA_ACTOR,
                              topic=NO_ANSWER_TOPIC.format(topic=ring.topic),
                              source=ContactSource.PERSONA, contact_id=contact.id)
            return True
        finish_contact(contact, ContactStatus.FAILED, f"звонок сорвался: {error or status}", now)
        return False

    @staticmethod
    async def record(session: AsyncSession, row: Call, status: CallStatus, *,
                     error: str | None, ended_at: datetime, end_reason: str = "",
                     extra: dict[str, Any] | None = None) -> None:
        """Final state of the row + the `call` event, in the caller's transaction."""
        row.status = status
        row.error = error
        row.ended_at = ended_at
        data: dict[str, Any] = {"direction": CallDirection(row.direction).value,
                                "status": status.value, "duration_s": row.duration_s,
                                "lines": len(row.transcript or []), **(extra or {})}
        if error:
            data["error"] = error
        if end_reason:
            data["end_reason"] = end_reason
        await append_event(session, chat_id=row.chat_id, kind=EventKind.CALL,
                           actor=ACTORS[CallDirection(row.direction)], call_id=row.id,
                           data=data)

    async def close_stale(self) -> int:
        """At process start: calls left ringing/active by a crash or restart become failed."""
        async with transaction(self.sessions) as session:
            rows = (await session.scalars(
                select(Call).where(Call.status.in_(OPEN_STATUSES)).order_by(Call.id))).all()
            for row in rows:
                await self.record(session, row, CallStatus.FAILED,
                                  error="interrupted by a restart", ended_at=self.clock.now())
        return len(rows)

    @staticmethod
    async def finish_live(session: LiveSession) -> dict[str, Any]:
        try:
            return dict(await session.finish() or {})
        except Exception as exc:
            log.warning("live session finish failed: %s", type(exc).__name__)
            return {}


def short(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:300]}"
