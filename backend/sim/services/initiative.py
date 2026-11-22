"""She writes and calls first (docs/initiative.md).

A `planned_contact` is a message or a call at `due_at` about `topic`: planned by the manager in
the dashboard («Запланировать») or by her in a reply turn (the plan's `call`: «давай
созвонимся» -> she dials within a minute). Every minute `agent.contacts_tick` starts the due
ones (`start_due`):

- a message: an `initiative` event («ты решила написать первой: <тема>») joins her timeline and
  a reply turn is asked for at once; the event waits for her answer like a client message
  (sim/services/turn_state.py), so the turn runs even with nothing new from the client;
- a call: the row becomes `started` and the telegram process dials (`telegram:place_call`,
  sim/telegram/calls/service.py `CallService.dial`), which closes the row with the outcome.
  Nobody picked up -> she texts later (an `initiative` event about the missed call).

Her own call needs the chat's «Звонит сама» switch and stays rare (sim/domain/initiative.py);
the manager's call needs only the calls to work at all. A started call whose job was lost is
failed after `STALE_START`."""
from __future__ import annotations

import logging
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.db.models import Call, Channel, Chat, Persona, PlannedContact
from sim.db.session import transaction
from sim.domain.agents import AgentKey, agent_config
from sim.domain.enums import (
    CallDirection,
    CallStatus,
    ChannelKind,
    ContactKind,
    ContactSource,
    ContactStatus,
    EventKind,
)
from sim.domain.initiative import (
    CONTACT_KEY,
    DIAL_DELAY_S,
    KIND_KEY,
    MAX_TOPIC,
    SOURCE_KEY,
    STALE_START,
    TOPIC_KEY,
    CallOutState,
    call_out_block,
)
from sim.events import append_event
from sim.services.context.facts import to_local
from sim.services.queue import after_commit

log = logging.getLogger(__name__)

PERSONA_ACTOR = "persona"
DUE_BATCH = 20
OPEN_CONTACTS = (ContactStatus.PLANNED, ContactStatus.STARTED)
OPEN_CALLS = (CallStatus.RINGING, CallStatus.ACTIVE)
PAST_GRACE = timedelta(minutes=1)          # «now» typed in the form is not «in the past»


class InitiativeError(ValueError):
    """The contact can not be planned; the message is for the manager."""


class ContactTasks(Protocol):
    async def reply(self, chat_id: int, delay_s: float = 0) -> None: ...

    async def place_call(self, contact_id: int) -> None: ...


@dataclass(frozen=True)
class Started:
    """What `start_due` handed to the queues."""

    replies: tuple[int, ...] = ()           # chat ids
    calls: tuple[int, ...] = ()             # contact ids


# --- whether calls can run at all ----------------------------------------------------------


def calls_unavailable(persona: Persona, channel_kind: ChannelKind, *,
                      calls_enabled: bool) -> str:
    """Why no call can run in this chat ("" = it can): the process, her switch, the model,
    the messenger (only Telegram has calls)."""
    if not calls_enabled:
        return "звонки выключены на сервере"
    if channel_kind != ChannelKind.TELEGRAM_USER:
        return "звонки есть только в Telegram"
    if not persona.calls_enabled:
        return "звонки выключены у персоны"
    if not agent_config(persona, AgentKey.LIVE).model:
        return "не выбрана модель звонка"
    return ""


async def channel_kind_of(session: AsyncSession, chat: Chat) -> ChannelKind:
    channel = await session.get(Channel, chat.channel_id)
    return ChannelKind(channel.kind) if channel is not None else ChannelKind.TELEGRAM_USER


async def call_out_state(session: AsyncSession, chat: Chat, persona: Persona, now: datetime,
                         *, channel_kind: ChannelKind, calls_enabled: bool) -> CallOutState:
    local = to_local(now, persona.timezone)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    mine = (Call.chat_id == chat.id, Call.direction == CallDirection.OUT)
    last = await session.scalar(select(func.max(Call.created_at)).where(*mine))
    today = await session.scalar(select(func.count()).select_from(Call)
                                 .where(*mine, Call.created_at >= midnight))
    pending = await session.scalar(
        select(func.count()).select_from(PlannedContact)
        .where(PlannedContact.chat_id == chat.id, PlannedContact.kind == ContactKind.CALL,
               PlannedContact.status.in_(OPEN_CONTACTS)))
    ringing = await session.scalar(select(func.count()).select_from(Call)
                                   .where(Call.chat_id == chat.id, Call.status.in_(OPEN_CALLS)))
    return CallOutState(
        switched_on=bool(chat.calls_out),
        unavailable=calls_unavailable(persona, channel_kind, calls_enabled=calls_enabled),
        local=local, last_call_at=last, calls_today=int(today or 0),
        pending=bool(pending or ringing), now=now)


async def her_call_block(session: AsyncSession, chat: Chat, persona: Persona, now: datetime,
                         *, channel_kind: ChannelKind, calls_enabled: bool) -> str:
    """Why she may not call this client now ("" = she may), for the reply model's facts."""
    return call_out_block(await call_out_state(session, chat, persona, now,
                                               channel_kind=channel_kind,
                                               calls_enabled=calls_enabled))


# --- planning ------------------------------------------------------------------------------


def clean_topic(topic: str) -> str:
    text = " ".join((topic or "").split())
    if not text:
        raise InitiativeError("Напиши тему: о чём написать или зачем звонок")
    if len(text) > MAX_TOPIC:
        raise InitiativeError(f"Тема длиннее {MAX_TOPIC} знаков")
    return text


async def plan_contact(session: AsyncSession, chat: Chat, *, kind: ContactKind, topic: str,
                       due_at: datetime, source: ContactSource, created_by: str,
                       now: datetime, calls_enabled: bool) -> PlannedContact:
    """A new planned contact (the caller commits). The manager's time must not be in the past;
    a call must be possible in this chat at all."""
    text = clean_topic(topic)
    if source == ContactSource.MANAGER and due_at < now - PAST_GRACE:
        raise InitiativeError("Это время уже прошло")
    if kind == ContactKind.CALL:
        persona = await session.get(Persona, chat.persona_id)
        if persona is None:
            raise InitiativeError("У чата нет персонажа")
        reason = calls_unavailable(persona, await channel_kind_of(session, chat),
                                   calls_enabled=calls_enabled)
        if reason:
            raise InitiativeError(reason[:1].upper() + reason[1:])
    contact = PlannedContact(chat_id=chat.id, kind=kind, source=source, topic=text,
                             due_at=max(due_at, now), created_by=created_by,
                             status=ContactStatus.PLANNED, result="")
    session.add(contact)
    await session.flush()
    return contact


async def plan_her_call(session: AsyncSession, chat_id: int, topic: str, now: datetime,
                        uniform: Any = None, in_minutes: int = 0) -> PlannedContact:
    """The reply plan's `call`: she dials within a minute, or `in_minutes` later (the facts
    allowed it this turn)."""
    pick = uniform or secrets.SystemRandom().uniform
    chat = await session.get(Chat, chat_id)
    if chat is None:
        raise InitiativeError("chat is gone")
    contact = PlannedContact(
        chat_id=chat.id, kind=ContactKind.CALL, source=ContactSource.PERSONA,
        topic=clean_topic(topic),
        due_at=now + timedelta(seconds=max(pick(*DIAL_DELAY_S), in_minutes * 60)),
        created_by=PERSONA_ACTOR, status=ContactStatus.PLANNED, result="")
    session.add(contact)
    await session.flush()
    return contact


def cancel_contact(contact: PlannedContact, now: datetime) -> None:
    if contact.status != ContactStatus.PLANNED:
        raise InitiativeError("Отменить можно только то, что ещё не началось")
    contact.status = ContactStatus.CANCELLED
    contact.finished_at = now


def finish_contact(contact: PlannedContact, status: ContactStatus, result: str,
                   now: datetime) -> None:
    contact.status = status
    contact.result = result[:2000]
    contact.finished_at = now


async def fail_started(sessions: async_sessionmaker[AsyncSession], contact_id: int,
                       reason: str, now: datetime) -> None:
    """A started call that can not be placed at all (no calls in the telegram process)."""
    async with transaction(sessions) as session:
        contact = await session.get(PlannedContact, contact_id, with_for_update=True)
        if contact is not None and contact.status == ContactStatus.STARTED:
            finish_contact(contact, ContactStatus.FAILED, reason, now)


async def chat_contacts(session: AsyncSession, chat_id: int,
                        limit: int = 50) -> Sequence[PlannedContact]:
    return (await session.scalars(
        select(PlannedContact).where(PlannedContact.chat_id == chat_id)
        .order_by(PlannedContact.due_at.desc(), PlannedContact.id.desc()).limit(limit))).all()


def contact_view(contact: PlannedContact) -> dict[str, Any]:
    return {
        "id": contact.id, "chat_id": contact.chat_id, "kind": ContactKind(contact.kind).value,
        "source": ContactSource(contact.source).value,
        "status": ContactStatus(contact.status).value, "topic": contact.topic,
        "due_at": contact.due_at.isoformat(), "result": contact.result,
        "created_by": contact.created_by, "created_at": contact.created_at.isoformat(),
        "finished_at": contact.finished_at.isoformat() if contact.finished_at else None,
        "call_id": contact.call_id,
    }


# --- the minute tick -----------------------------------------------------------------------


async def write_first(session: AsyncSession, chat_id: int, *, topic: str,
                      source: ContactSource, actor: str,
                      contact_id: int | None = None) -> int:
    """The `initiative` event: she decided to write first about `topic`. Returns its id."""
    event = await append_event(
        session, chat_id=chat_id, kind=EventKind.INITIATIVE, actor=actor,
        data={CONTACT_KEY: contact_id, KIND_KEY: ContactKind.MESSAGE.value,
              TOPIC_KEY: topic, SOURCE_KEY: source.value})
    return event.id


async def start_due(session: AsyncSession, now: datetime) -> Started:
    """Starts the due contacts (the caller commits, then queues `Started`) and fails the
    started calls whose job was lost."""
    rows = (await session.scalars(
        select(PlannedContact)
        .where(PlannedContact.status == ContactStatus.PLANNED, PlannedContact.due_at <= now)
        .order_by(PlannedContact.due_at, PlannedContact.id).limit(DUE_BATCH)
        .with_for_update(skip_locked=True))).all()
    replies: list[int] = []
    calls: list[int] = []
    for contact in rows:
        if contact.kind == ContactKind.MESSAGE:
            contact.event_id = await write_first(
                session, contact.chat_id, topic=contact.topic,
                source=ContactSource(contact.source),
                actor=contact.created_by or PERSONA_ACTOR, contact_id=contact.id)
            finish_contact(contact, ContactStatus.DONE, "она написала первой", now)
            replies.append(contact.chat_id)
        else:
            contact.status = ContactStatus.STARTED
            calls.append(contact.id)
    # A call job lost before it dialled, or a call a restart cut off (its row is closed by
    # CallService.close_stale, its contact was not). Not the ones started just now: an overdue
    # contact (the worker was down) has its job queued this very tick.
    lost = (await session.scalars(
        select(PlannedContact)
        .outerjoin(Call, Call.id == PlannedContact.call_id)
        .where(PlannedContact.status == ContactStatus.STARTED,
               PlannedContact.due_at < now - STALE_START, PlannedContact.id.not_in(calls),
               or_(PlannedContact.call_id.is_(None), Call.status.not_in(OPEN_CALLS)))
        .with_for_update(of=PlannedContact, skip_locked=True))).all()
    for contact in lost:
        finish_contact(contact, ContactStatus.FAILED, "звонок прервался или не начался", now)
    await session.flush()
    return Started(replies=tuple(replies), calls=tuple(calls))


async def queue_started(tasks: ContactTasks, started: Started) -> None:
    for chat_id in started.replies:
        await after_commit(f"chat {chat_id}: initiative turn", tasks.reply(chat_id, 0))
    for contact_id in started.calls:
        await after_commit(f"contact {contact_id}: call", tasks.place_call(contact_id))
