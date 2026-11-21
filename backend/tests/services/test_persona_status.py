"""«Статус Марины» for the team (sim/services/persona_status.py, docs/attention.md, «Статус для
команды»): every branch of the pure `describe` in priority order, the batched loading of many
chats against the database, and that the status never reaches the reply agent's context."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.config import Settings
from sim.db.models import Chat, Client, Generation, Message, Persona, ReplyRun
from sim.domain.attention import AttentionMode, AttentionSettings
from sim.domain.enums import (
    Author,
    Direction,
    EventKind,
    GenerationKind,
    GenerationStatus,
    MessageKind,
    MessageStatus,
    RunStatus,
)
from sim.domain.life import BusyLevel
from sim.services.attention.timing import Availability
from sim.services.context.builder import ContextBuilder
from sim.services.deps import Deps
from sim.services.persona_status import (
    ChatFacts,
    LastRun,
    Moment,
    PersonaStatus,
    StatusCode,
    StatusTone,
    chat_status,
    chat_statuses,
    describe,
    load_facts,
)
from sim.services.turn_state import Waiting
from tests.services.factories import add_event, add_message, make_chat, make_persona
from tests.services.life_seed import SATURDAY, SEED, TZ, persona, utc

NOW = utc(SATURDAY, "19:30")                # the test clock: Saturday 19:30 in Berlin
WAITING = Waiting(count=2, last_event_id=50)


def a_chat(**fields: Any) -> Chat:
    return Chat(**{"id": 7, "persona_id": 1, "client_id": 3, "ai_enabled": True,
                   "needs_operator": False, "attention_instant": False, **fields})


def a_client(**fields: Any) -> Client:
    return Client(**{"id": 3, "persona_id": 1, "name": "Дима", "ai_enabled": True, **fields})


def marina(**fields: Any) -> Persona:
    return persona(**{"is_active": True, "ai_enabled": True, "attention": {},
                      "attention_check_at": None, **fields})


def facts(**fields: Any) -> ChatFacts:
    return ChatFacts(**{"chat": a_chat(), "client": a_client(), "persona": marina(), **fields})


def moment(level: BusyLevel = BusyLevel.FREE, *, settings: AttentionSettings | None = None,
           **av: Any) -> Moment:
    return Moment(now=NOW, settings=settings or AttentionSettings(),
                  av=Availability(level=level, **av))


def look(action: str, *, until: int = 50, later: str | None = None,
         reason: str = "после созвона") -> dict[str, Any]:
    return {"action": action, "reason": reason, "until_event_id": until,
            "later_at": utc(SATURDAY, later).isoformat() if later else None}


# --- 1-2. nobody answers -----------------------------------------------------------------------


def test_ai_off_says_where_it_is_off() -> None:
    cases = [
        (facts(persona=marina(is_active=False)), StatusCode.ARCHIVED,
         "Марина в архиве — не отвечает"),
        (facts(persona=marina(ai_enabled=False)), StatusCode.AI_OFF_PERSONA,
         "ИИ выключен у персонажа — Марина не отвечает"),
        (facts(client=a_client(ai_enabled=False)), StatusCode.AI_OFF_CLIENT,
         "ИИ выключен у клиента — Марина не отвечает"),
        (facts(chat=a_chat(ai_enabled=False, needs_operator=True)), StatusCode.AI_OFF_CHAT,
         "ИИ выключен в этом чате — Марина не отвечает"),
    ]
    for chat_facts, code, title in cases:
        status = describe(chat_facts, moment())
        assert (status.code, status.tone, status.title) == (code, StatusTone.OFF, title)
        assert status.detail                        # where to switch it on


def test_no_persona_is_off() -> None:
    status = describe(facts(persona=None), None)
    assert status.code == StatusCode.NO_PERSONA and status.tone == StatusTone.OFF


def test_an_operator_is_needed_with_the_guard_reason() -> None:
    status = describe(facts(chat=a_chat(needs_operator=True), waiting=WAITING,
                            operator=("guard", "назвала фамилию")), moment())
    assert status.code == StatusCode.NEEDS_OPERATOR and status.tone == StatusTone.WARN
    assert status.title == "Нужен оператор: охранник заблокировал ответ"
    assert status.detail.startswith("назвала фамилию · ")
    plain = describe(facts(chat=a_chat(needs_operator=True)), moment())
    assert plain.title == "Нужен оператор"


# --- 3-6. answering right now ------------------------------------------------------------------


def test_a_running_turn_is_thinking_and_a_stale_one_is_not() -> None:
    running = LastRun(status=RunStatus.RUNNING, created_at=NOW - timedelta(seconds=20))
    status = describe(facts(run=running, waiting=WAITING), moment())
    assert (status.code, status.title) == (StatusCode.THINKING, "Думает над ответом")
    assert "19:29" in status.detail                 # her local time
    stale = LastRun(status=RunStatus.RUNNING, created_at=NOW - timedelta(minutes=30))
    assert describe(facts(run=stale), moment()).code == StatusCode.ANSWERED


def test_a_generation_and_a_pending_send_by_kind() -> None:
    for kind, title in ((GenerationKind.VIDEO_NOTE, "Записывает кружок"),
                        (GenerationKind.VIDEO, "Снимает видео"),
                        (GenerationKind.PHOTO, "Делает фото"),
                        (GenerationKind.VOICE, "Записывает голосовое")):
        status = describe(facts(generation=kind, sending=MessageKind.TEXT), moment())
        assert (status.code, status.title) == (StatusCode.GENERATING, title)
    for message_kind, title in ((MessageKind.TEXT, "Печатает ответ"),
                                (MessageKind.VOICE, "Записывает голосовое")):
        status = describe(facts(sending=message_kind, waiting=WAITING), moment())
        assert (status.code, status.tone, status.title) == (StatusCode.SENDING, StatusTone.OK,
                                                            title)


def test_a_failed_turn_for_the_waiting_messages_needs_a_person() -> None:
    failed = LastRun(status=RunStatus.FAILED, created_at=NOW, trigger_event_id=50,
                     error="PortError: timeout")
    status = describe(facts(run=failed, waiting=WAITING), moment())
    assert status.code == StatusCode.TURN_FAILED and status.tone == StatusTone.WARN
    assert status.detail.startswith("PortError: timeout")
    older = LastRun(status=RunStatus.FAILED, created_at=NOW, trigger_event_id=40)
    assert describe(facts(run=older, waiting=WAITING), moment()).code == StatusCode.FREE


# --- 7. her decision ---------------------------------------------------------------------------


def test_reply_later_names_her_local_time_and_the_reason() -> None:
    status = describe(facts(waiting=WAITING, look=look("reply_later", later="20:40")),
                      moment())
    assert status == PersonaStatus(
        code=StatusCode.REPLY_LATER, tone=StatusTone.WAIT,
        title="Прочитала, ответит около 20:40", detail="после созвона",
        until=utc(SATURDAY, "20:40"), local_until="20:40")
    due = describe(facts(waiting=WAITING, look=look("reply_later", later="19:20")), moment())
    assert due.code == StatusCode.PROMISE_DUE
    assert due.title == "Обещала ответить в 19:20 — вот-вот ответит"


def test_every_other_decision() -> None:
    cases = [
        ("read_only", StatusCode.READ_ONLY, "Оставила прочитанным"),
        ("leave_unread", StatusCode.LEAVE_UNREAD, "Не открывала чат"),
        ("text_after_missed_call", StatusCode.AFTER_MISSED_CALL,
         "Напишет после пропущенного звонка"),
        ("reply_now", StatusCode.REPLY_NOW, "Сейчас ответит"),
    ]
    for action, code, title in cases:
        status = describe(facts(waiting=WAITING, look=look(action)), moment())
        assert (status.code, status.title) == (code, title)
        assert status.detail.startswith("после созвона")


def test_a_decision_older_than_the_waiting_messages_is_not_used() -> None:
    status = describe(facts(waiting=WAITING, look=look("read_only", until=40)), moment())
    assert status.code == StatusCode.FREE


# --- 8-9. not looked yet -----------------------------------------------------------------------


def test_instant_in_the_chat_or_for_the_persona() -> None:
    chat_instant = describe(facts(chat=a_chat(attention_instant=True), waiting=WAITING),
                            moment(BusyLevel.AWAY))
    assert chat_instant.code == StatusCode.INSTANT and chat_instant.title == "Ответит сразу"
    assert chat_instant.detail.startswith("«Отвечать сразу» в этом чате")
    persona_instant = describe(facts(waiting=WAITING), moment(
        settings=AttentionSettings(mode=AttentionMode.INSTANT)))
    assert persona_instant.detail.startswith("режим «Всегда сразу»")


def test_away_partial_and_free_by_her_availability() -> None:
    away = describe(facts(waiting=WAITING, persona=marina(
        attention_check_at=utc(SATURDAY, "21:01"))), moment(
        BusyLevel.AWAY, activity="пробежка", ends_at=utc(SATURDAY, "21:00")))
    assert (away.code, away.tone) == (StatusCode.AWAY, StatusTone.BUSY)
    assert away.title == "Занята до 21:00 (пробежка) — увидит после"
    assert away.detail == "без ответа: 2 · заглянет в телефон около 21:01"
    assert away.local_until == "21:00"

    partial = describe(facts(waiting=WAITING, persona=marina(
        attention_check_at=utc(SATURDAY, "19:40"))), moment(
        BusyLevel.PARTIAL, activity="созвон с Соней", ends_at=utc(SATURDAY, "20:30")))
    assert partial.title == "Урывками (созвон с Соней) — заглянет в телефон около 19:40"
    assert partial.detail == "без ответа: 2 · занята до 20:30"
    assert partial.until == utc(SATURDAY, "19:40")

    free = describe(facts(waiting=WAITING), moment())
    assert (free.code, free.tone) == (StatusCode.FREE, StatusTone.WAIT)
    assert free.title == "Свободна — заглянет в течение минуты" and free.until is None


def test_a_past_pending_check_is_ignored() -> None:
    status = describe(facts(waiting=WAITING, persona=marina(
        attention_check_at=NOW - timedelta(minutes=5))), moment(
        BusyLevel.PARTIAL, activity="рынок", ends_at=utc(SATURDAY, "20:00")))
    assert status.title == "Урывками (рынок) — заглянет в телефон около 20:00"


# --- 10. nothing waits -------------------------------------------------------------------------


def test_all_answered_says_what_she_does_now() -> None:
    status = describe(facts(), moment(activity="чтение Канемана", place="дома, спальня",
                                      ends_at=utc(SATURDAY, "23:00")))
    assert (status.code, status.tone) == (StatusCode.ANSWERED, StatusTone.OK)
    assert status.title == "Всё отвечено · сейчас: чтение Канемана"
    assert status.detail == "дома, спальня · до 23:00"
    assert status.as_json() == {
        "code": "answered", "tone": "ok", "title": status.title, "detail": status.detail,
        "until": utc(SATURDAY, "23:00"), "local_until": "23:00"}


# --- against the database ----------------------------------------------------------------------


async def _rows(db: AsyncSession,
                chats: list[Chat]) -> list[tuple[Chat, Client, Persona | None]]:
    rows = []
    for chat in chats:
        client = await db.get(Client, chat.client_id)
        assert client is not None
        rows.append((chat, client, await db.get(Persona, chat.persona_id)))
    return rows


async def test_many_chats_at_once(db: AsyncSession, clock: FixedClock, deps: Deps) -> None:
    marina_db = await make_persona(db, timezone=TZ, life=SEED["life"])
    now = clock.now()
    answered = await make_chat(db, marina_db, name="Отвеченный")
    await add_message(db, answered, "привет", at=now - timedelta(minutes=9))
    await add_message(db, answered, "привет!", at=now - timedelta(minutes=8),
                      direction=Direction.OUT)

    later = await make_chat(db, marina_db, name="Позже")
    _, seen = await add_message(db, later, "как дела?", at=now - timedelta(minutes=5))
    await add_event(db, later, EventKind.ATTENTION, at=now - timedelta(minutes=4), data={
        "action": "reply_later", "reason": "после ужина", "until_event_id": seen.id,
        "later_at": utc(SATURDAY, "20:40").isoformat()})

    thinking = await make_chat(db, marina_db, name="Думает")
    _, asked = await add_message(db, thinking, "ты тут?", at=now - timedelta(minutes=1))
    db.add(ReplyRun(chat_id=thinking.id, status=RunStatus.RUNNING,
                    trigger_event_id=asked.id, created_at=now - timedelta(seconds=10)))

    blocked = await make_chat(db, marina_db, name="Оператор")
    blocked.needs_operator = True
    await add_event(db, blocked, EventKind.OPERATOR_NEEDED, at=now,
                    data={"reason": "назвала адрес"})

    sending = await make_chat(db, marina_db, name="Кружок")
    await add_message(db, sending, "покажись", at=now - timedelta(minutes=2))
    db.add(Message(chat_id=sending.id, direction=Direction.OUT, author=Author.PERSONA,
                   kind=MessageKind.VIDEO_NOTE, text="", status=MessageStatus.PENDING))

    photo = await make_chat(db, marina_db, name="Фото")
    db.add(Generation(persona_id=marina_db.id, chat_id=photo.id, kind=GenerationKind.PHOTO,
                      prompt="селфи", status=GenerationStatus.RUNNING, created_at=now))

    failed = await make_chat(db, marina_db, name="Сбой")
    _, broken = await add_message(db, failed, "ау", at=now - timedelta(minutes=3))
    db.add(ReplyRun(chat_id=failed.id, status=RunStatus.FAILED, trigger_event_id=broken.id,
                    error="PortError: timeout", created_at=now - timedelta(minutes=2)))

    missed = await make_chat(db, marina_db, name="Звонил")
    await add_event(db, missed, EventKind.CALL, at=now - timedelta(minutes=1),
                    data={"status": "missed", "life_busy": "away", "life_activity": "пробежка"})
    await db.flush()

    chats = [answered, later, thinking, blocked, sending, photo, failed, missed]
    statuses = await chat_statuses(db, await _rows(db, chats), now)

    assert {chat.id: statuses[chat.id].title for chat in chats} == {
        answered.id: "Всё отвечено · сейчас: свободное время",
        later.id: "Прочитала, ответит около 20:40",
        thinking.id: "Думает над ответом",
        blocked.id: "Нужен оператор",
        sending.id: "Записывает кружок",
        photo.id: "Делает фото",
        failed.id: "Ответ не получился",
        missed.id: "Свободна — заглянет в течение минуты",
    }
    assert statuses[blocked.id].detail.startswith("назвала адрес")
    assert statuses[missed.id].detail == "без ответа: 1"
    one = await chat_status(db, deps, later.id)
    assert one == statuses[later.id]


async def test_a_generation_is_now_only_within_its_window(db: AsyncSession,
                                                          clock: FixedClock) -> None:
    # One definition with the reply context (sim/services/generation_state.py): a video claimed
    # two hours ago is no longer «Снимает видео» — the reaper fails it.
    her = await make_persona(db)
    stale, fresh = await make_chat(db, her), await make_chat(db, her, name="Саша")
    for chat, kind, ago in ((stale, GenerationKind.VIDEO, timedelta(hours=2)),
                            (fresh, GenerationKind.PHOTO, timedelta(minutes=10))):
        db.add(Generation(persona_id=chat.persona_id, chat_id=chat.id, kind=kind,
                          prompt="селфи", status=GenerationStatus.RUNNING,
                          created_at=clock.now() - ago, updated_at=clock.now() - ago))
    await db.flush()

    facts = await load_facts(db, await _rows(db, [stale, fresh]), clock.now())

    assert facts[stale.id].generation is None
    assert facts[fresh.id].generation == GenerationKind.PHOTO


async def test_her_routine_decides_when_she_sees_it(db: AsyncSession, clock: FixedClock,
                                                    deps: Deps) -> None:
    """Saturday 20:10 in Berlin she talks to Sonya (busy until 20:30)."""
    marina_db = await make_persona(db, timezone=TZ, life=SEED["life"],
                                   attention_check_at=utc(SATURDAY, "20:18"))
    chat = await make_chat(db, marina_db)
    clock.at = utc(SATURDAY, "20:10")
    await add_message(db, chat, "привет", at=clock.now())
    status = await chat_status(db, deps, chat.id)
    assert status.title == "Урывками (созвон с Соней) — заглянет в телефон около 20:18"
    assert status.local_until == "20:18"


async def test_the_status_never_reaches_the_reply_context(db: AsyncSession, clock: FixedClock,
                                                          settings: Settings,
                                                          deps: Deps) -> None:
    chat = await make_chat(db)
    _, seen = await add_message(db, chat, "как дела?", at=clock.now() - timedelta(minutes=5))
    await add_event(db, chat, EventKind.ATTENTION, at=clock.now() - timedelta(minutes=4), data={
        "action": "reply_later", "who": "Марина", "reason": "после созвона с подругой",
        "until_event_id": seen.id,
        "later_at": (clock.now() + timedelta(minutes=30)).isoformat()})
    status = await chat_status(db, deps, chat.id)
    assert status.code == StatusCode.REPLY_LATER

    ctx = await ContextBuilder(clock, settings).for_reply(db, chat.id)

    seen_by_agent = "\n".join(part.text or "" for message in ctx.messages
                              for part in message.parts)
    seen_by_agent += json.dumps(ctx.snapshot, ensure_ascii=False, default=str)
    for text in (status.title, status.detail):
        assert text not in seen_by_agent
