"""Incoming private calls answered by the persona's live voice (docs/calls.md).

  ring -> resolve the chat -> may she answer? (no: decline, recorded)
       -> wait a human ring delay -> open the Broker live session (before picking up: when the
          Broker refuses, nobody picked up) -> answer -> talk (AudioBridge) -> hang up
       -> Call row + `call` event in one transaction -> refresh the client's memory.

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
from sim.domain.enums import CallDirection, CallStatus, EventKind
from sim.events import append_event
from sim.ports import ClockPort, LivePort, LiveSession, Peer
from sim.telegram.calls.backend import CallGone, IncomingCall
from sim.telegram.calls.bridge import AudioBridge, Outcome

log = logging.getLogger(__name__)

ACTOR = "client"                       # an incoming call is the client's action
OPEN_STATUSES = (CallStatus.RINGING, CallStatus.ACTIVE)


class ChatResolver(Protocol):
    async def resolve_chat(self, session: AsyncSession, channel_id: int, peer: Peer) -> Chat: ...


class CallContextSource(Protocol):
    async def for_call(self, session: AsyncSession, chat_id: int) -> Any: ...


class MemoryQueue(Protocol):
    async def refresh_memory(self, chat_id: int) -> Any: ...


def refuse_reason(*, calls_enabled: bool, persona: Persona | None, client: Client | None,
                  chat: Chat) -> str | None:
    """Why she must not pick up, or None. Checked in this order, first reason wins."""
    if not calls_enabled:
        return "calls are disabled (SIM_CALLS_ENABLED)"
    if persona is None or not persona.ai_enabled:
        return "persona AI is off"
    if not persona.calls_enabled:
        return "persona calls are off"
    if not persona.model_live:
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


class CallService:
    def __init__(
        self, *, sessions: async_sessionmaker[AsyncSession], settings: Settings,
        clock: ClockPort, live: LivePort, inbox: ChatResolver, context: CallContextSource,
        tasks: MemoryQueue, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
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
            await call.decline()
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
                greeting=ctx.greeting, max_seconds=max_seconds)
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
            await self.tasks.refresh_memory(ring.chat_id)
        except Exception:
            log.exception("call %s: memory refresh not queued", ring.call_id)
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
                     error: str | None, ended_at: datetime, end_reason: str = "") -> None:
        """Final state of the row + the `call` event, in the caller's transaction."""
        row.status = status
        row.error = error
        row.ended_at = ended_at
        data: dict[str, Any] = {"direction": CallDirection(row.direction).value,
                                "status": status.value, "duration_s": row.duration_s,
                                "lines": len(row.transcript or [])}
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
