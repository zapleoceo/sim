"""«Статус Марины» for the team (docs/attention.md, «Статус для команды»): what the persona is
doing about one chat right now and why there is a pause, so a manager sees that a delay is
intended, not a hang. Computed on read from what is already stored; never stored, never sent
to the client and never part of any agent's context.

First match wins:

1. AI off — the persona is archived, or AI is off for the persona, the client or the chat;
2. an operator is needed (with the reason of the last `operator_needed` event);
   (1-2 are the reasons of the AI gate, `sim.domain.ai_gate`, in its order)
3. a reply turn is running — «Думает над ответом»;
4. a generation for this chat is running — «Записывает кружок», «Делает фото»…;
5. a message waits to be sent — «Печатает ответ», «Записывает голосовое»…;
6. the turn for the waiting messages failed;
7. her phone-check decision covering the waiting messages (the newest `attention` event);
8. «Всегда сразу» or the chat's «Отвечать сразу» — she answers without a phone check;
9. waiting messages she has not looked at yet — by her availability now;
10. nothing waits — «Всё отвечено · сейчас: <what she does>».

`describe` is pure. `chat_statuses` loads the facts of many chats in a fixed number of queries
and her availability once per persona."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, ChatEvent, Client, Generation, Message, Persona, ReplyRun
from sim.domain.ai_gate import AiBlock, ai_block
from sim.domain.attention import AttentionAction, AttentionSettings, attention_settings
from sim.domain.enums import (
    Direction,
    EventKind,
    GenerationKind,
    GenerationStatus,
    MessageKind,
    MessageStatus,
    RunStatus,
)
from sim.domain.life import BusyLevel
from sim.services.attention.notices import RUNNING_FRESH
from sim.services.attention.timing import Availability, availability, local_hhmm
from sim.services.deps import Deps
from sim.services.life.store import load_life
from sim.services.turn_state import Waiting, answered_bounds, waiting_counts

A = AttentionAction
GENERATION_FRESH = timedelta(minutes=30)    # an older unfinished generation is not «now»
GUARD_ACTOR = "guard"                       # `chat_event.actor` of the guard's block
ERROR_CHARS = 200


class StatusTone(StrEnum):
    OK = "ok"           # all answered, or she is answering right now
    WAIT = "wait"       # an intended pause: she will answer, or chose not to
    BUSY = "busy"       # her life keeps her away from the phone
    WARN = "warn"       # a person is needed
    OFF = "off"         # the AI does not answer here


class StatusCode(StrEnum):
    NO_PERSONA = "no_persona"
    ARCHIVED = "archived"
    AI_OFF_PERSONA = "ai_off_persona"
    AI_OFF_CLIENT = "ai_off_client"
    AI_OFF_CHAT = "ai_off_chat"
    NEEDS_OPERATOR = "needs_operator"
    THINKING = "thinking"
    GENERATING = "generating"
    SENDING = "sending"
    TURN_FAILED = "turn_failed"
    REPLY_NOW = "reply_now"
    REPLY_LATER = "reply_later"
    PROMISE_DUE = "promise_due"
    READ_ONLY = "read_only"
    LEAVE_UNREAD = "leave_unread"
    AFTER_MISSED_CALL = "after_missed_call"
    INSTANT = "instant"
    AWAY = "away"
    PARTIAL = "partial"
    FREE = "free"
    ANSWERED = "answered"


@dataclass(frozen=True)
class PersonaStatus:
    """One short Russian line (`title`), the reason (`detail`) and when the pause ends (UTC and
    her local «HH:MM»)."""

    code: StatusCode
    tone: StatusTone
    title: str
    detail: str = ""
    until: datetime | None = None
    local_until: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {"code": self.code.value, "tone": self.tone.value, "title": self.title,
                "detail": self.detail, "until": self.until, "local_until": self.local_until}


@dataclass(frozen=True)
class LastRun:
    """The newest reply turn of a chat."""

    status: RunStatus
    created_at: datetime
    trigger_event_id: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class ChatFacts:
    """Everything the status of one chat is decided from."""

    chat: Chat
    client: Client
    persona: Persona | None
    run: LastRun | None = None
    generation: GenerationKind | None = None        # running for this chat
    sending: MessageKind | None = None              # the oldest outgoing message not sent yet
    waiting: Waiting | None = None                  # unanswered client messages / missed calls
    look: Mapping[str, Any] | None = None           # data of the newest `attention` event
    operator: tuple[str, str] | None = None         # (actor, reason) of `operator_needed`


@dataclass(frozen=True)
class Moment:
    """Her side, once per persona: now, the owner's settings and her availability."""

    now: datetime
    settings: AttentionSettings
    av: Availability


def _status(code: StatusCode, tone: StatusTone, title: str, detail: str = "",
            until: datetime | None = None, tz: str | None = None) -> PersonaStatus:
    return PersonaStatus(code=code, tone=tone, title=title, detail=detail, until=until,
                         local_until=local_hhmm(until, tz) if until is not None else None)


def _join(*parts: str) -> str:
    return " · ".join(part for part in parts if part)


def _unanswered(waiting: Waiting) -> str:
    return f"без ответа: {waiting.count}"


# --- 1-2. nobody answers -----------------------------------------------------------------------


NO_PERSONA_TITLE = "Персонаж не найден"
NO_PERSONA_DETAIL = "в этом чате некому отвечать"
# Each «AI off» reason of the AI gate (sim.domain.ai_gate): code, title (`{name}` = hers) and
# where to switch it on.
AI_OFF: dict[AiBlock, tuple[StatusCode, str, str]] = {
    AiBlock.NO_PERSONA: (StatusCode.NO_PERSONA, NO_PERSONA_TITLE, NO_PERSONA_DETAIL),
    AiBlock.ARCHIVED: (StatusCode.ARCHIVED, "{name} в архиве — не отвечает",
                       "вернуть из архива можно на странице персонажа"),
    AiBlock.PERSONA_OFF: (StatusCode.AI_OFF_PERSONA,
                          "ИИ выключен у персонажа — {name} не отвечает",
                          "переключатель «ИИ» на странице персонажа"),
    AiBlock.CLIENT_OFF: (StatusCode.AI_OFF_CLIENT, "ИИ выключен у клиента — {name} не отвечает",
                         "переключатель «ИИ» в карточке лида"),
    AiBlock.CHAT_OFF: (StatusCode.AI_OFF_CHAT, "ИИ выключен в этом чате — {name} не отвечает",
                       "переключатель «ИИ в чате» в шапке переписки"),
}


def _blocked(f: ChatFacts, persona: Persona, m: Moment) -> PersonaStatus | None:
    """Rules 1-2: the AI gate says she must not answer here — where the AI is off, or that an
    operator is needed."""
    blocked = ai_block(persona, f.client, f.chat)
    if blocked is None:
        return None
    if blocked == AiBlock.NEEDS_OPERATOR:
        return _operator(f)
    code, title, detail = AI_OFF[blocked]
    return _status(code, StatusTone.OFF, title.format(name=persona.name), detail)


def _operator(f: ChatFacts) -> PersonaStatus:
    actor, reason = f.operator or ("", "")
    title = ("Нужен оператор: охранник заблокировал ответ" if actor == GUARD_ACTOR
             else "Нужен оператор")
    return _status(StatusCode.NEEDS_OPERATOR, StatusTone.WARN, title,
                   _join(reason, "ИИ молчит, пока не нажмут «Оператор разобрался»"))


# --- 3-6. she is answering right now (or the answer failed) ------------------------------------

GENERATING = {
    GenerationKind.VIDEO_NOTE: "Записывает кружок",
    GenerationKind.VIDEO: "Снимает видео",
    GenerationKind.PHOTO: "Делает фото",
    GenerationKind.VOICE: "Записывает голосовое",
}
SENDING = {
    MessageKind.TEXT: "Печатает ответ",
    MessageKind.VOICE: "Записывает голосовое",
    MessageKind.VIDEO_NOTE: "Записывает кружок",
    MessageKind.PHOTO: "Отправляет фото",
    MessageKind.VIDEO: "Отправляет видео",
    MessageKind.FILE: "Отправляет файл",
    MessageKind.STICKER: "Отправляет стикер",
}


def _turn(f: ChatFacts, persona: Persona, m: Moment) -> PersonaStatus | None:
    run = f.run
    if run is None or run.status != RunStatus.RUNNING or run.created_at < m.now - RUNNING_FRESH:
        return None
    started = local_hhmm(run.created_at, persona.timezone)
    return _status(StatusCode.THINKING, StatusTone.OK, "Думает над ответом",
                   f"ход ответа идёт с {started} · клиент видит «печатает…»")


def _generation(f: ChatFacts, persona: Persona, m: Moment) -> PersonaStatus | None:
    if f.generation is None:
        return None
    return _status(StatusCode.GENERATING, StatusTone.OK,
                   GENERATING.get(f.generation, "Готовит вложение"),
                   "готовит вложение к ответу — уйдёт клиенту, когда будет готово")


def _sending(f: ChatFacts, persona: Persona, m: Moment) -> PersonaStatus | None:
    if f.sending is None:
        return None
    return _status(StatusCode.SENDING, StatusTone.OK, SENDING.get(f.sending, "Отправляет"),
                   "сообщение уходит в Telegram")


def _failed(f: ChatFacts, persona: Persona, m: Moment) -> PersonaStatus | None:
    run, waiting = f.run, f.waiting
    if (waiting is None or run is None or run.status != RunStatus.FAILED
            or (run.trigger_event_id or 0) < waiting.last_event_id):
        return None
    error = (run.error or "ошибка хода ответа")[:ERROR_CHARS]
    return _status(StatusCode.TURN_FAILED, StatusTone.WARN, "Ответ не получился",
                   _join(error, "ответьте сами или дождитесь следующего сообщения клиента"))


# --- 7-9. messages wait for her ----------------------------------------------------------------


def _later_at(look: Mapping[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(look.get("later_at") or ""))
    except ValueError:
        return None


def _decided(f: ChatFacts, persona: Persona, m: Moment) -> PersonaStatus | None:
    """Her phone-check decision, when it covers every waiting message."""
    look, waiting = f.look, f.waiting
    if (waiting is None or look is None
            or int(look.get("until_event_id") or 0) < waiting.last_event_id):
        return None
    action, reason, tz = look.get("action"), str(look.get("reason") or ""), persona.timezone
    if action == A.REPLY_LATER:
        at = _later_at(look)
        if at is None:
            return _status(StatusCode.REPLY_LATER, StatusTone.WAIT, "Прочитала, ответит позже",
                           reason)
        hhmm = local_hhmm(at, tz)
        if at > m.now:
            return _status(StatusCode.REPLY_LATER, StatusTone.WAIT,
                           f"Прочитала, ответит около {hhmm}", reason, at, tz)
        return _status(StatusCode.PROMISE_DUE, StatusTone.WAIT,
                       f"Обещала ответить в {hhmm} — вот-вот ответит", reason, at, tz)
    if action == A.READ_ONLY:
        return _status(StatusCode.READ_ONLY, StatusTone.WAIT, "Оставила прочитанным",
                       _join(reason, "ответит, если клиент напишет ещё"))
    if action == A.LEAVE_UNREAD:
        return _status(StatusCode.LEAVE_UNREAD, StatusTone.WAIT, "Не открывала чат",
                       _join(reason, "посмотрит, когда клиент напишет ещё"))
    if action in (A.TEXT_AFTER_MISSED_CALL, A.CALL_BACK):
        return _status(StatusCode.AFTER_MISSED_CALL, StatusTone.OK,
                       "Напишет после пропущенного звонка", reason)
    if action == A.REPLY_NOW:
        return _status(StatusCode.REPLY_NOW, StatusTone.OK, "Сейчас ответит", reason)
    return None


def _instant(f: ChatFacts, persona: Persona, m: Moment) -> PersonaStatus | None:
    if f.waiting is None or not m.settings.is_instant(f.chat.attention_instant):
        return None
    why = ("«Отвечать сразу» в этом чате" if f.chat.attention_instant
           else "режим «Всегда сразу»")
    return _status(StatusCode.INSTANT, StatusTone.OK, "Ответит сразу",
                   _join(why, f"ждёт пачку сообщений {persona.reply_delay_s} с"))


def _not_seen(f: ChatFacts, persona: Persona, m: Moment) -> PersonaStatus | None:
    """Waiting messages she has not looked at: when her life lets her pick up the phone."""
    waiting = f.waiting
    if waiting is None:
        return None
    av, tz = m.av, persona.timezone
    pending = persona.attention_check_at
    check = pending if pending is not None and pending > m.now else None
    if av.level == BusyLevel.AWAY:
        end = av.ends_at
        title = (f"Занята до {local_hhmm(end, tz)} ({av.activity}) — увидит после"
                 if end is not None else f"Занята ({av.activity}) — увидит позже")
        glance = f"заглянет в телефон около {local_hhmm(check, tz)}" if check else ""
        return _status(StatusCode.AWAY, StatusTone.BUSY, title,
                       _join(_unanswered(waiting), glance), end, tz)
    if av.level == BusyLevel.PARTIAL:
        at = check or av.ends_at
        when = f"около {local_hhmm(at, tz)}" if at is not None else "скоро"
        busy_till = f"занята до {local_hhmm(av.ends_at, tz)}" if av.ends_at else ""
        return _status(StatusCode.PARTIAL, StatusTone.BUSY,
                       f"Урывками ({av.activity}) — заглянет в телефон {when}",
                       _join(_unanswered(waiting), busy_till), at, tz)
    return _status(StatusCode.FREE, StatusTone.WAIT, "Свободна — заглянет в течение минуты",
                   _unanswered(waiting), check, tz)


def _answered(f: ChatFacts, persona: Persona, m: Moment) -> PersonaStatus:
    av, tz = m.av, persona.timezone
    company = f"с {', '.join(av.with_whom)}" if av.with_whom else ""
    till = f"до {local_hhmm(av.ends_at, tz)}" if av.ends_at is not None else ""
    return _status(StatusCode.ANSWERED, StatusTone.OK, f"Всё отвечено · сейчас: {av.activity}",
                   _join(av.place, company, till), av.ends_at, tz)


Rule = Callable[[ChatFacts, Persona, Moment], PersonaStatus | None]
RULES: tuple[Rule, ...] = (_blocked, _turn, _generation, _sending, _failed, _decided,
                           _instant, _not_seen)


def describe(f: ChatFacts, m: Moment | None) -> PersonaStatus:
    """The status of one chat from its facts and her moment (None: no persona)."""
    persona = f.persona
    if persona is None or m is None:
        return _status(StatusCode.NO_PERSONA, StatusTone.OFF, NO_PERSONA_TITLE,
                       NO_PERSONA_DETAIL)
    for rule in RULES:
        status = rule(f, persona, m)
        if status is not None:
            return status
    return _answered(f, persona, m)


# --- loading -----------------------------------------------------------------------------------

ChatRow = tuple[Chat, Client, Persona | None]


async def _last_runs(session: AsyncSession, ids: Sequence[int]) -> dict[int, LastRun]:
    rows = await session.execute(
        select(ReplyRun.chat_id, ReplyRun.status, ReplyRun.created_at,
               ReplyRun.trigger_event_id, ReplyRun.error)
        .where(ReplyRun.chat_id.in_(ids))
        .distinct(ReplyRun.chat_id).order_by(ReplyRun.chat_id, ReplyRun.id.desc()))
    return {chat_id: LastRun(status=status, created_at=created_at, trigger_event_id=trigger,
                             error=error)
            for chat_id, status, created_at, trigger, error in rows.all()}


async def _generations(session: AsyncSession, ids: Sequence[int],
                       now: datetime) -> dict[int, GenerationKind]:
    rows = await session.execute(
        select(Generation.chat_id, Generation.kind)
        .where(Generation.chat_id.in_(ids),
               Generation.status.in_((GenerationStatus.QUEUED, GenerationStatus.RUNNING)),
               Generation.created_at >= now - GENERATION_FRESH)
        .distinct(Generation.chat_id).order_by(Generation.chat_id, Generation.id.desc()))
    return {chat_id: kind for chat_id, kind in rows.all() if chat_id is not None}


async def _sending(session: AsyncSession, ids: Sequence[int]) -> dict[int, MessageKind]:
    rows = await session.execute(
        select(Message.chat_id, Message.kind)
        .where(Message.chat_id.in_(ids), Message.direction == Direction.OUT,
               Message.status == MessageStatus.PENDING)
        .distinct(Message.chat_id).order_by(Message.chat_id, Message.id))
    return {chat_id: kind for chat_id, kind in rows.all()}


async def _newest_events(session: AsyncSession, ids: Sequence[int],
                         kind: EventKind) -> dict[int, tuple[str, dict[str, Any]]]:
    """The newest event of `kind` per chat: (actor, data)."""
    if not ids:
        return {}
    rows = await session.execute(
        select(ChatEvent.chat_id, ChatEvent.actor, ChatEvent.data)
        .where(ChatEvent.chat_id.in_(ids), ChatEvent.kind == kind)
        .distinct(ChatEvent.chat_id).order_by(ChatEvent.chat_id, ChatEvent.id.desc()))
    return {chat_id: (actor, data or {}) for chat_id, actor, data in rows.all()}


async def load_facts(session: AsyncSession, rows: Sequence[ChatRow],
                     now: datetime) -> dict[int, ChatFacts]:
    """The facts of many chats in a fixed number of queries, whatever their count."""
    ids = [chat.id for chat, _, _ in rows]
    if not ids:
        return {}
    runs = await _last_runs(session, ids)
    generations = await _generations(session, ids, now)
    sending = await _sending(session, ids)
    waiting = await waiting_counts(session, await answered_bounds(session, ids))
    looks = await _newest_events(session, ids, EventKind.ATTENTION)
    operators = await _newest_events(
        session, [chat.id for chat, _, _ in rows if chat.needs_operator],
        EventKind.OPERATOR_NEEDED)
    facts: dict[int, ChatFacts] = {}
    for chat, client, persona in rows:
        look = looks.get(chat.id)
        operator = operators.get(chat.id)
        facts[chat.id] = ChatFacts(
            chat=chat, client=client, persona=persona, run=runs.get(chat.id),
            generation=generations.get(chat.id), sending=sending.get(chat.id),
            waiting=waiting.get(chat.id), look=look[1] if look is not None else None,
            operator=(operator[0], str(operator[1].get("reason") or ""))
            if operator is not None else None)
    return facts


async def moment_of(session: AsyncSession, persona: Persona, now: datetime) -> Moment:
    settings = attention_settings(persona.attention)
    return Moment(now=now, settings=settings,
                  av=availability(await load_life(session, persona, now), settings))


async def chat_statuses(session: AsyncSession, rows: Sequence[ChatRow],
                        now: datetime) -> dict[int, PersonaStatus]:
    """The status of every chat in `rows`, by chat id; her availability once per persona."""
    facts = await load_facts(session, rows, now)
    personas = {persona.id: persona for _, _, persona in rows if persona is not None}
    moments = {pid: await moment_of(session, persona, now) for pid, persona in personas.items()}
    return {chat.id: describe(facts[chat.id], moments.get(chat.persona_id))
            for chat, _, _ in rows}


async def chat_status(session: AsyncSession, deps: Deps, chat_id: int) -> PersonaStatus:
    """The status of one chat now (`deps.clock`). LookupError when there is no such chat."""
    chat = await session.get(Chat, chat_id)
    client = await session.get(Client, chat.client_id) if chat is not None else None
    if chat is None or client is None:
        raise LookupError(f"chat {chat_id} not found")
    persona = await session.get(Persona, chat.persona_id)
    return (await chat_statuses(session, [(chat, client, persona)], deps.clock.now()))[chat.id]
