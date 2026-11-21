"""Incoming private calls answered by the persona's live voice (docs/calls.md).

  ring -> resolve the chat -> may she answer? (no: decline, recorded)
       -> is she free? (her availability after the attention settings, docs/attention.md:
          away — it rings unanswered, missed; busy — she may decline; either way the `call`
          event carries `life_busy` and her phone check decides when she texts back)
       -> wait a human ring delay -> open the Broker live session (before picking up: when the
          Broker refuses, nobody picked up) -> answer -> talk (AudioBridge) -> hang up
       -> Call row + `call` event in one transaction (the passport learns it at night).

Every outcome is a `call` row and a `call` event: declined, missed (the caller hung up first),
failed (Broker or Telegram refused), ended (a conversation took place). No database transaction
is open while the call runs; nothing slow except the live session runs in this process."""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.config import Settings
from sim.db.models import Call, Chat, Client, Persona
from sim.db.session import transaction
from sim.domain.agents import AgentKey, agent_config
from sim.domain.attention import attention_settings
from sim.domain.enums import CallDirection, CallStatus, EventKind
from sim.domain.life import BusyLevel
from sim.events import append_event
from sim.ports import ClockPort, LivePort, LiveSession, Peer
from sim.services.attention.schedule import check_after_notice, queue_check
from sim.services.attention.timing import Availability, availability
from sim.services.life.store import load_life
from sim.services.turn_state import LIFE_ACTIVITY_KEY, LIFE_REFUSED_KEY
from sim.telegram.calls.backend import CallGone, IncomingCall
from sim.telegram.calls.bridge import AudioBridge, Outcome

log = logging.getLogger(__name__)

ACTOR = "client"                       # an incoming call is the client's action
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


def life_refusal(av: Availability, uniform: Callable[[float, float], float]) -> Refusal | None:
    """Decided at ring time from her availability after the attention settings
    (docs/attention.md): away (asleep, running) — she does not pick up, a missed call; busy —
    she may decline (`PARTIAL_DECLINE_SHARE`); free — she answers after the ring delay."""
    if av.level == BusyLevel.AWAY:
        return Refusal(CallStatus.MISSED, av.level, av.activity)
    if av.level == BusyLevel.PARTIAL and uniform(0.0, 1.0) < PARTIAL_DECLINE_SHARE:
        return Refusal(CallStatus.DECLINED, av.level, av.activity)
    return None


def refuse_reason(*, calls_enabled: bool, persona: Persona | None, client: Client | None,
                  chat: Chat) -> str | None:
    """Why she must not pick up, or None. Checked in this order, first reason wins."""
    if not calls_enabled:
        return "calls are disabled (SIM_CALLS_ENABLED)"
    if persona is None or persona.id != chat.persona_id or not persona.is_active \
            or not persona.ai_enabled:
        return "persona AI is off (or the persona is archived)"
    if not persona.calls_enabled:
        return "persona calls are off"
    if not agent_config(persona, AgentKey.LIVE).model:
        return "no live model chosen for the persona"
    if client is None or not client.ai_enabled:
        return "AI is off for this client"
    if not chat.ai_enabled:
        return "AI is off in this chat"
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


class CallService:
    def __init__(
        self, *, sessions: async_sessionmaker[AsyncSession], settings: Settings,
        clock: ClockPort, live: LivePort, inbox: ChatResolver, context: CallContextSource,
        tasks: CallTasks, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        uniform: Callable[[float, float], float] | None = None,
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

    async def talk(self, ring: Ring, call: IncomingCall, session: LiveSession,
                   max_seconds: int) -> CallStatus:
        started_at, started = self.clock.now(), self.mono()
        await self.mark_active(ring.call_id, started_at)
        try:
            outcome = await AudioBridge(call, session, max_seconds=max_seconds,
                                        monotonic=self.mono).run()
        except Exception as exc:          # never leave the row open
            outcome = Outcome("bridge_error", short(exc), [])
        finally:
            summary = await self.finish_live(session)
            await call.hangup()
        duration = max(0.0, self.mono() - started)
        if not outcome.frames_out:
            log.warning("call %s: the caller never heard her (%s)", ring.call_id, outcome.reason)
        cost = summary.get("cost_usd")
        status = await self.close(
            ring, CallStatus.ENDED, error=outcome.error, started_at=started_at,
            duration_s=round(duration, 1),
            transcript=outcome.transcript or broker_transcript(summary),
            cost_usd=float(cost) if isinstance(cost, int | float) else None,
            model=str(summary.get("model") or getattr(ring.context, "model", "") or ""),
            end_reason=outcome.reason)
        try:
            # A call that made today too long for the context compresses its morning.
            await self.tasks.refresh_memory(ring.chat_id)
        except Exception:
            log.exception("call %s: morning compression not queued", ring.call_id)
        return status

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
                    end_reason: str = "") -> CallStatus:
        ended_at = (started_at + timedelta(seconds=duration_s)
                    if started_at is not None and duration_s is not None else self.clock.now())
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
                              end_reason=end_reason)
        return status

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
        await append_event(session, chat_id=row.chat_id, kind=EventKind.CALL, actor=ACTOR,
                           call_id=row.id, data=data)

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
