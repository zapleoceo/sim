"""What she sees when she picks up her phone (docs/attention.md, «Что она видит»): every chat of
the persona with something new since her last look — client messages after her last turn,
calls she did not take because of her life, edits — and the answers she promised that are due.

A chat's «last look» is its newest `attention` event: `data.until_event_id` is the newest event
that decision covered. Together with the answered boundary (`turn_state.answered_until`) it
splits the chat into what she has seen and what is new."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, ChatEvent, Client, Message, Persona, ReplyRun
from sim.domain.attention import AttentionAction, AttentionSettings
from sim.domain.enums import Direction, EventKind, MessageKind, RunStatus
from sim.domain.passport import Passport, passport_of
from sim.services.attention.schedule import DUE_SLACK
from sim.services.attention.timing import local_hhmm
from sim.services.context.render import MEDIA_LABELS, one_line
from sim.services.turn_state import (
    LIFE_ACTIVITY_KEY,
    LIFE_REFUSED_KEY,
    Unanswered,
    ai_allowed,
    answered_until,
    unanswered,
)

NOTICE_WINDOW = timedelta(days=2)       # older activity is not «new» any more
RUNNING_FRESH = timedelta(minutes=10)   # a turn running this recently: she is in that chat
NEWS_KINDS = (EventKind.MESSAGE_IN, EventKind.MESSAGE_EDITED, EventKind.CALL)
LAST_LINES = 6
LINE_CHARS = 200


@dataclass(frozen=True)
class MissedCall:
    at: datetime
    status: str                         # missed | declined
    activity: str                       # what she was doing


@dataclass(frozen=True)
class Promise:
    """Her last decision in the chat was «answer later at `at`» and she has not answered."""

    at: datetime


@dataclass
class ChatNotice:
    chat_id: int
    client_name: str
    passport: Passport
    instant: bool                       # the chat's «Отвечать сразу»
    until_event_id: int                 # the newest event this look covers
    new_messages: int = 0
    edits: int = 0
    missed_calls: list[MissedCall] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)      # new client texts (urgency hints)
    unanswered: Unanswered | None = None
    lines: list[str] = field(default_factory=list)      # the last messages, oldest first
    last_from: str = ""                 # client | persona
    last_out_at: datetime | None = None
    promise: Promise | None = None

    @property
    def has_news(self) -> bool:
        return bool(self.new_messages or self.edits or self.missed_calls)

    def promise_due(self, now: datetime) -> bool:
        return self.promise is not None and self.promise.at <= now + DUE_SLACK


async def collect_notices(session: AsyncSession, persona: Persona, now: datetime,
                          settings: AttentionSettings) -> list[ChatNotice]:
    """The chats with something for her to look at, by chat id."""
    chat_ids = list(await session.scalars(
        select(ChatEvent.chat_id).join(Chat, Chat.id == ChatEvent.chat_id)
        .where(Chat.persona_id == persona.id,
               ChatEvent.kind.in_((*NEWS_KINDS, EventKind.ATTENTION)),
               ChatEvent.created_at >= now - NOTICE_WINDOW)
        .distinct().order_by(ChatEvent.chat_id)))
    notices = []
    for chat_id in chat_ids:
        notice = await chat_notice(session, persona, chat_id, now, settings)
        if notice is not None and (notice.has_news or notice.promise_due(now)):
            notices.append(notice)
    return notices


async def chat_notice(session: AsyncSession, persona: Persona, chat_id: int, now: datetime,
                      settings: AttentionSettings) -> ChatNotice | None:
    """One chat as she sees it now; None when she must not or need not look at it (AI off,
    an operator is needed, she is writing there right now)."""
    chat = await session.get(Chat, chat_id)
    client = await session.get(Client, chat.client_id) if chat is not None else None
    if chat is None or not ai_allowed(persona, client, chat) or chat.needs_operator:
        return None
    if await _turn_running(session, chat_id, now):
        return None
    answered = await answered_until(session, chat_id)
    last = await _last_look(session, chat_id)
    seen = int((last.data or {}).get("until_event_id") or 0) if last is not None else 0
    events = list(await session.scalars(
        select(ChatEvent).where(ChatEvent.chat_id == chat_id,
                                ChatEvent.id > max(answered, seen),
                                ChatEvent.kind.in_(NEWS_KINDS))
        .order_by(ChatEvent.id)))
    notice = ChatNotice(
        chat_id=chat_id, client_name=client.name or "собеседник",
        passport=passport_of(client.passport), instant=chat.attention_instant,
        until_event_id=max([seen, answered, *(event.id for event in events)]),
        unanswered=await unanswered(session, chat_id),
        promise=_promise(last, answered))
    await _read_news(session, notice, events, settings)
    await _read_lines(session, notice, persona)
    return notice


async def _turn_running(session: AsyncSession, chat_id: int, now: datetime) -> bool:
    found = (await session.execute(
        select(ReplyRun.id).where(ReplyRun.chat_id == chat_id,
                                  ReplyRun.status == RunStatus.RUNNING,
                                  ReplyRun.created_at >= now - RUNNING_FRESH)
        .limit(1))).scalar_one_or_none()
    return found is not None


async def _last_look(session: AsyncSession, chat_id: int) -> ChatEvent | None:
    return (await session.scalars(
        select(ChatEvent).where(ChatEvent.chat_id == chat_id,
                                ChatEvent.kind == EventKind.ATTENTION)
        .order_by(ChatEvent.id.desc()).limit(1))).first()


def _promise(last: ChatEvent | None, answered: int) -> Promise | None:
    """A «later» that is still open: no turn answered what that look covered."""
    data = (last.data or {}) if last is not None else {}
    if data.get("action") != AttentionAction.REPLY_LATER or not data.get("later_at"):
        return None
    if answered >= int(data.get("until_event_id") or 0):
        return None
    try:
        return Promise(at=datetime.fromisoformat(str(data["later_at"])))
    except ValueError:
        return None


async def _read_news(session: AsyncSession, notice: ChatNotice, events: list[ChatEvent],
                     settings: AttentionSettings) -> None:
    message_ids = [e.message_id for e in events
                   if e.kind == EventKind.MESSAGE_IN and e.message_id is not None]
    messages = {m.id: m for m in await session.scalars(
        select(Message).where(Message.id.in_(message_ids)))} if message_ids else {}
    for event in events:
        data = event.data or {}
        if event.kind == EventKind.MESSAGE_IN:
            notice.new_messages += 1
            message = messages.get(event.message_id or 0)
            if message is not None and message.text.strip():
                notice.texts.append(one_line(message.text))
        elif event.kind == EventKind.MESSAGE_EDITED:
            notice.edits += 1
            notice.texts.append(one_line(str(data.get("new", ""))))
        elif LIFE_REFUSED_KEY in data and settings.after_missed_call:
            notice.missed_calls.append(MissedCall(
                at=event.created_at, status=str(data.get("status", "missed")),
                activity=str(data.get(LIFE_ACTIVITY_KEY) or data.get(LIFE_REFUSED_KEY))))


async def _read_lines(session: AsyncSession, notice: ChatNotice, persona: Persona) -> None:
    rows = list(await session.scalars(
        select(Message).where(Message.chat_id == notice.chat_id)
        .order_by(Message.id.desc()).limit(LAST_LINES)))
    for message in reversed(rows):
        incoming = message.direction == Direction.IN
        who = notice.client_name if incoming else persona.name
        at = local_hhmm(message.sent_at or message.created_at, persona.timezone)
        notice.lines.append(f"[{at}] {who}: {_body(message)}")
    if rows:
        notice.last_from = "client" if rows[0].direction == Direction.IN else "persona"
    notice.last_out_at = (await session.execute(
        select(func.max(func.coalesce(Message.sent_at, Message.created_at)))
        .where(Message.chat_id == notice.chat_id, Message.direction == Direction.OUT)
    )).scalar_one_or_none()


def _body(message: Message) -> str:
    text = one_line(message.text)[:LINE_CHARS]
    if message.kind == MessageKind.TEXT:
        return text or "…"
    label = f"[{MEDIA_LABELS.get(message.kind, 'вложение')}]"
    return f"{label} {text}" if text else label
