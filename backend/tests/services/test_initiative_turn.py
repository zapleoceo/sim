"""She writes and calls first, as the reply model sees it (docs/initiative.md): the facts say
whether she may call herself, a due message is a line of her timeline and a turn of its own
with no client message, the plan's `call` becomes her planned call (or is sent back when she
may not call), the context of her own call, and her phone check after a call she talked on."""
from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.call_scope import current_scope
from sim.clock import FixedClock
from sim.config import Settings
from sim.db.models import Call, Chat, ChatEvent, Persona, PlannedContact
from sim.domain.attention import AttentionAction, AttentionSettings
from sim.domain.enums import (
    CallDirection,
    CallStatus,
    ContactKind,
    ContactSource,
    ContactStatus,
    Direction,
    EventKind,
    RunStatus,
)
from sim.ports import ChatMessage
from sim.services.attention.check import PhoneCheck
from sim.services.attention.notices import chat_notice
from sim.services.attention.schedule import request_check
from sim.services.capabilities import Capabilities, with_call_out
from sim.services.context.builder import ContextBuilder
from sim.services.context.facts import FactsInput, compute_facts
from sim.services.context.instructions import CALL_OUT_ANY
from sim.services.context.render import Related, Speakers, render_event
from sim.services.deps import Deps
from sim.services.plan_check import PlanError, validated_plan
from sim.services.reply import ReplyService, turn_scope_name
from tests.services.conftest import Fakes
from tests.services.factories import (
    add_event,
    add_message,
    all_rows,
    make_chat,
    make_persona,
    ok_verdict,
    plan_json,
)

NOW = datetime(2026, 11, 21, 18, 30, tzinfo=UTC)      # 20:30 in Kyiv
ALL = Capabilities(voice=True, photo=True, photo_generate=True, video=True, video_generate=True)
TEXT = {"type": "text", "text": "привет!"}
TOPIC = "договорились созвониться"


def with_call(*messages: dict[str, Any], topic: str = TOPIC) -> str:
    return json.dumps({"messages": list(messages), "call": {"topic": topic}},
                      ensure_ascii=False)


def every_text(messages: Sequence[ChatMessage]) -> str:
    return "\n".join(part.text or "" for message in messages for part in message.parts)


def prompt(fakes: Fakes, n: int = 0) -> str:
    """Everything the reply model got in its `n`-th call of the turn."""
    return every_text(fakes.llm.calls_for("reply-model")[n]["messages"])


# --- pure: the facts line, the timeline line, the plan check -------------------------------------


def test_the_facts_say_whether_she_may_call_herself() -> None:
    def line(caps: Capabilities) -> str:
        text = compute_facts(FactsInput(now=NOW, persona_tz="Europe/Kyiv",
                                        capabilities=caps)).text
        return next(row for row in text.splitlines() if row.startswith("- Сейчас можно"))

    assert line(with_call_out(ALL, "")).endswith("; позвонить самой — да.")
    assert line(with_call_out(ALL, "ты недавно уже звонила")).endswith(
        "; позвонить самой — нет (ты недавно уже звонила).")
    assert line(ALL).endswith("кружки — да; позвонить самой — нет.")
    caps = with_call_out(ALL, "в этом чате звонить самой нельзя")
    assert (caps.call_out, caps.call_block) == (False, "в этом чате звонить самой нельзя")
    assert caps.as_dict()["call_out"] is False


def test_a_due_message_is_a_line_of_her_timeline() -> None:
    who = Speakers(client="Дима", persona="Марина", tz="Europe/Kyiv")

    def event(data: dict[str, Any]) -> ChatEvent:
        return ChatEvent(id=1, chat_id=1, kind=EventKind.INITIATIVE, in_context=True,
                         created_at=NOW, data=data, actor="operator:1")

    assert render_event(event({"topic": " спросить\nпро отпуск "}), Related(), who) == (
        "[21.11 20:30] (ты решила написать первой: спросить про отпуск)")
    assert render_event(event({"topic": ""}), Related(), who) is None


def test_a_call_is_checked_against_the_facts() -> None:
    blocked = with_call_out(ALL, "сейчас слишком поздно или рано для звонка")
    with pytest.raises(PlanError) as info:
        validated_plan(with_call(TEXT), catalog={}, caps=blocked)
    assert info.value.errors == ["call: звонить сейчас нельзя (сейчас слишком поздно или рано "
                                 "для звонка); верни call: null и ответь сообщением"]
    plan = validated_plan(with_call(TEXT), catalog={}, caps=with_call_out(ALL, ""))
    assert plan.call is not None and plan.call.topic == TOPIC


def test_turn_scope_of_a_turn_without_a_client_message() -> None:
    assert turn_scope_name(7, None, 42) == "reply-7-event-42"
    assert turn_scope_name(7, 5, 42) == "reply-7-5"                 # a message keys it
    assert turn_scope_name(7, None) == "reply-7-0"


# --- the reply turn ---------------------------------------------------------------------------


async def chat_with_message(db: AsyncSession, clock: FixedClock, *,
                            calls_out: bool = False) -> Chat:
    chat = await make_chat(db, await make_persona(db))
    chat.calls_out = calls_out
    await add_message(db, chat, "может, созвонимся?", at=clock.now() - timedelta(seconds=60))
    return chat


async def contacts_of(db: AsyncSession, chat: Chat) -> list[PlannedContact]:
    return await all_rows(db, select(PlannedContact).where(PlannedContact.chat_id == chat.id))


async def test_her_call_in_the_plan_becomes_a_planned_call(db: AsyncSession, deps: Deps,
                                                           fakes: Fakes,
                                                           clock: FixedClock) -> None:
    chat = await chat_with_message(db, clock, calls_out=True)
    fakes.llm.script("reply-model", with_call(TEXT)).script("guard-model", ok_verdict())

    result = await ReplyService(deps).run(chat.id)

    assert result.status == RunStatus.SENT
    user = prompt(fakes)
    assert "позвонить самой — да" in user
    [planned] = await contacts_of(db, chat)
    assert (planned.kind, planned.source, planned.status, planned.created_by) == (
        ContactKind.CALL, ContactSource.PERSONA, ContactStatus.PLANNED, "persona")
    assert planned.topic == TOPIC
    # Within a minute (DIAL_DELAY_S); the minute tick dials it.
    assert clock.now() + timedelta(seconds=20) <= planned.due_at
    assert planned.due_at <= clock.now() + timedelta(seconds=75)
    assert len(fakes.tasks.of("send")) == 1


async def test_a_call_she_may_not_make_is_sent_back(db: AsyncSession, deps: Deps,
                                                    fakes: Fakes, clock: FixedClock) -> None:
    chat = await chat_with_message(db, clock)            # «Звонит сама» is off
    fakes.llm.script("reply-model", with_call(TEXT), plan_json(TEXT))
    fakes.llm.script("guard-model", ok_verdict())

    assert (await ReplyService(deps).run(chat.id)).status == RunStatus.SENT

    first = prompt(fakes)
    assert "позвонить самой — нет (в этом чате звонить самой нельзя)" in first
    repair = fakes.llm.calls_for("reply-model")[1]["messages"][-1].parts[0].text or ""
    assert "звонить сейчас нельзя" in repair
    assert await contacts_of(db, chat) == []


async def test_a_due_message_is_answered_without_a_client_message(db: AsyncSession,
                                                                  deps: Deps, fakes: Fakes,
                                                                  clock: FixedClock) -> None:
    chat = await make_chat(db, await make_persona(db))
    await add_message(db, chat, "пока!", at=clock.now() - timedelta(hours=3))
    await add_message(db, chat, "пока)", at=clock.now() - timedelta(hours=3),
                      direction=Direction.OUT)
    due = await add_event(db, chat, EventKind.INITIATIVE, at=clock.now() - timedelta(minutes=1),
                          data={"contact_id": None, "kind": "message",
                                "topic": "спросить, как прошло собеседование",
                                "source": "manager"})
    scopes: list[str | None] = []

    def answer(record: dict[str, Any]) -> str:
        scope = current_scope()
        scopes.append(scope.name if scope is not None else None)
        return plan_json({"type": "text", "text": "ну как собеседование?"})

    fakes.llm.script("reply-model", answer).script("guard-model", ok_verdict())

    result = await ReplyService(deps).run(chat.id)

    assert result.status == RunStatus.SENT
    user = prompt(fakes)
    assert "(ты решила написать первой: спросить, как прошло собеседование)" in user
    assert scopes == [f"reply-{chat.id}-event-{due.id}"]
    assert fakes.tasks.of("mark_read") == []            # nothing of his to read
    assert len(fakes.tasks.of("send")) == 1
    # Answered: the next turn finds nothing.
    assert (await ReplyService(deps).run(chat.id)).reason == "nothing to answer"


# --- the context of her own call ----------------------------------------------------------------


async def test_the_context_of_her_own_call(db: AsyncSession, clock: FixedClock,
                                           settings: Settings) -> None:
    chat = await make_chat(db, await make_persona(db, voice_speed=1.3))
    builder = ContextBuilder(clock, settings)

    incoming = await builder.for_call(db, chat.id)
    assert incoming.greeting == "Алло?" and "Это ты позвонила" not in incoming.system
    assert incoming.speed == 1.3

    mine = await builder.for_call(db, chat.id, outgoing_topic=f"  {TOPIC} ")
    assert mine.greeting == ""                          # she waits for his «алло»
    assert "# Это ты позвонила" in mine.system and f"Повод: {TOPIC}." in mine.system
    anyhow = await builder.for_call(db, chat.id, outgoing_topic="  ")
    assert f"Повод: {CALL_OUT_ANY}." in anyhow.system


async def test_the_reply_context_knows_the_chat_switch(db: AsyncSession, clock: FixedClock,
                                                       settings: Settings) -> None:
    chat = await chat_with_message(db, clock, calls_out=True)
    ctx = await ContextBuilder(clock, settings).for_reply(db, chat.id)
    assert ctx.capabilities.call_out and ctx.capabilities.call_block == ""
    assert "позвонить самой — да." in every_text(ctx.messages)

    persona = await db.get(Persona, chat.persona_id)
    assert persona is not None
    persona.calls_enabled = False
    await db.flush()
    off = await ContextBuilder(clock, settings).for_reply(db, chat.id)
    assert not off.capabilities.call_out
    assert off.capabilities.call_block == "звонки выключены у персоны"


# --- her phone check after a call she talked on -------------------------------------------------


async def test_after_a_talk_she_does_what_she_promised_at_once(db: AsyncSession, deps: Deps,
                                                               fakes: Fakes,
                                                               clock: FixedClock) -> None:
    persona = await make_persona(db)
    chat = await make_chat(db, persona)
    now = clock.now()                                   # 20:30 in Kyiv
    await add_message(db, chat, "давай созвонимся", at=now - timedelta(minutes=10))
    await add_message(db, chat, "давай!", at=now - timedelta(minutes=9),
                      direction=Direction.OUT)
    missed = Call(chat_id=chat.id, direction=CallDirection.OUT, status=CallStatus.MISSED,
                  created_at=now - timedelta(minutes=7))
    talk = Call(chat_id=chat.id, direction=CallDirection.IN, status=CallStatus.ENDED,
                duration_s=102, created_at=now - timedelta(minutes=5),
                transcript=[{"who": "client", "text": "привет", "t": 0},
                            {"who": "client", "text": "ну всё, жду кружок", "t": 90},
                            {"who": "persona", "text": "запишу,\nпока!", "t": 100}])
    db.add_all([missed, talk])
    await db.flush()
    await add_event(db, chat, EventKind.CALL, at=now - timedelta(minutes=3), call_id=talk.id,
                    data={"direction": "in", "status": "ended", "after_call": True})

    notice = await chat_notice(db, persona, chat.id, now, AttentionSettings())
    assert notice is not None and notice.after_call and notice.has_news
    assert notice.lines == [
        "[20:20] Дима: давай созвонимся",
        "[20:21] Марина: давай!",
        "[20:23] звонок от Марина: missed",
        "[20:25] звонок от Дима, поговорили 1:42 — в конце: Дима: привет / Дима: ну всё, жду "
        "кружок / Марина: запишу, пока!",
    ]

    assert await request_check(db, persona.id, now, now)
    result = await PhoneCheck(deps).run(persona.id)

    [decision] = result.decisions
    assert decision.action == AttentionAction.REPLY_NOW
    assert decision.reason == "только что говорили по телефону"
    assert fakes.tasks.of("reply") == [("reply", chat.id, 0)] and fakes.llm.calls == []
