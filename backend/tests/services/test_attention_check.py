"""The phone check against the database (sim/services/attention, docs/attention.md): one pending
check per persona (an earlier one wins), stale jobs, the rule shortcuts, the attention agent's
JSON and every action's effect, the fallback that never loses a message, missed calls, her
life (away, «не беспокоить») and the inbox: «Всегда сразу» and the chat's «Отвечать сразу»
answer exactly as before."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.db.models import Chat, ChatEvent, LlmCall, Persona
from sim.domain.agents import REGISTRY
from sim.domain.attention import AttentionAction, AttentionPlan
from sim.domain.enums import Direction, EventKind, LlmPurpose, RunStatus
from sim.ports import InboundMessage, Peer, PortError
from sim.services.attention.agent import NO_MODEL, UNREADABLE
from sim.services.attention.check import SKIPPED_CHAT, CheckResult, CheckStatus, PhoneCheck
from sim.services.attention.schedule import lost_checks, request_check
from sim.services.deps import Deps
from sim.services.inbox import Inbox
from sim.services.reply import ReplyService
from tests.services.conftest import Fakes
from tests.services.factories import (
    add_event,
    add_message,
    all_rows,
    fresh,
    make_chat,
    make_persona,
    ok_verdict,
    plan_json,
)
from tests.services.life_seed import SATURDAY, SEED, TZ, utc

ATTENTION_MODEL = REGISTRY["attention"].model
SUNDAY = SATURDAY + timedelta(days=1)
A = AttentionAction
PEER_ID = "555"


def plan(*decisions: dict[str, Any]) -> str:
    return json.dumps({"decisions": list(decisions)}, ensure_ascii=False)


def decision(chat: Chat, action: str, *, later: str | None = None,
             reason: str = "так решила") -> dict[str, Any]:
    return {"chat_id": chat.id, "action": action, "later": later, "reason": reason}


async def due_now(db: AsyncSession, persona: Persona, clock: FixedClock) -> None:
    """A check is due right now (as if its job just started)."""
    assert await request_check(db, persona.id, clock.now(), clock.now())


async def look(deps: Deps, persona: Persona) -> CheckResult:
    return await PhoneCheck(deps).run(persona.id)


async def looks(db: AsyncSession, chat: Chat) -> list[ChatEvent]:
    return await all_rows(db, select(ChatEvent).where(
        ChatEvent.chat_id == chat.id, ChatEvent.kind == EventKind.ATTENTION)
        .order_by(ChatEvent.id))


async def living(db: AsyncSession, **fields: Any) -> Persona:
    """Marina with the seed routine (Berlin): Saturday 20:00-20:30 she talks to her daughter
    (busy), at night she sleeps (away)."""
    return await make_persona(db, timezone=TZ, life=SEED["life"], **fields)


def agent_payload(fakes: Fakes) -> dict[str, Any]:
    call = fakes.llm.calls_for(ATTENTION_MODEL)[-1]
    return json.loads(call["messages"][1].parts[0].text or "{}")


def actions(result: CheckResult) -> list[AttentionAction]:
    return [d.action for d in result.decisions]


# --- one pending check -----------------------------------------------------------------------


def incoming(chat: Chat, clock: FixedClock, external_id: str) -> InboundMessage:
    return InboundMessage(channel_id=chat.channel_id, peer=Peer(external_id=PEER_ID),
                          external_id=external_id, text="привет", sent_at=clock.now())


async def test_a_message_asks_for_one_phone_check(db: AsyncSession, deps: Deps, fakes: Fakes,
                                                  clock: FixedClock) -> None:
    persona = await make_persona(db)
    chat = await make_chat(db, persona, external_id=PEER_ID)
    inbox = Inbox(deps)
    await inbox.ingest(incoming(chat, clock, "1"))
    await inbox.ingest(incoming(chat, clock, "2"))      # the pending check covers it

    # Free: a glance after the median 20 s; no reply turn yet — she has not looked.
    assert fakes.tasks.calls == [("phone_check", persona.id, 20.0)]
    assert (await fresh(db, persona)).attention_check_at == clock.now() + timedelta(seconds=20)


async def test_an_edit_is_a_notification_too(db: AsyncSession, deps: Deps, fakes: Fakes,
                                             clock: FixedClock) -> None:
    persona = await make_persona(db)
    chat = await make_chat(db, persona, external_id=PEER_ID)
    message, _ = await add_message(db, chat, "привет", at=clock.now())
    await Inbox(deps).edited(chat.channel_id, Peer(external_id=PEER_ID),
                             message.external_id or "", "приветик")
    assert fakes.tasks.calls == [("phone_check", persona.id, 20.0)]


async def test_an_earlier_check_wins_and_a_lost_one_is_replaced(db: AsyncSession,
                                                               clock: FixedClock) -> None:
    persona = await make_persona(db)
    now = clock.now()
    assert await request_check(db, persona.id, now + timedelta(minutes=10), now)
    assert not await request_check(db, persona.id, now + timedelta(minutes=15), now)
    assert await request_check(db, persona.id, now + timedelta(minutes=1), now)
    assert (await fresh(db, persona)).attention_check_at == now + timedelta(minutes=1)
    later = now + timedelta(minutes=5)                  # 4 minutes overdue: its job was lost
    assert await lost_checks(db, later) == [persona.id]
    assert await request_check(db, persona.id, later + timedelta(minutes=30), later)


async def test_a_stale_job_finds_nothing_due(db: AsyncSession, deps: Deps, fakes: Fakes,
                                             clock: FixedClock) -> None:
    persona = await make_persona(db)
    assert (await look(deps, persona)).status == CheckStatus.NOT_DUE
    await request_check(db, persona.id, clock.now() + timedelta(minutes=5), clock.now())
    assert (await look(deps, persona)).status == CheckStatus.NOT_DUE   # moved to later
    assert fakes.llm.calls == [] and fakes.tasks.calls == []


async def test_nothing_new_releases_the_check(db: AsyncSession, deps: Deps,
                                              clock: FixedClock) -> None:
    persona = await make_persona(db)
    chat = await make_chat(db, persona, chat_ai=False)
    await add_message(db, chat, "ау", at=clock.now())   # AI off in this chat: not hers to see
    await due_now(db, persona, clock)
    assert (await look(deps, persona)).status == CheckStatus.NOTHING
    assert (await fresh(db, persona)).attention_check_at is None


# --- rules -------------------------------------------------------------------------------------


async def test_the_chat_switch_replies_now_without_the_agent(db: AsyncSession, deps: Deps,
                                                             fakes: Fakes,
                                                             clock: FixedClock) -> None:
    persona = await make_persona(db)
    chat = await make_chat(db, persona)
    chat.attention_instant = True
    _, event = await add_message(db, chat, "привет", at=clock.now() - timedelta(seconds=30))
    await due_now(db, persona, clock)

    result = await look(deps, persona)

    assert result.status == CheckStatus.DONE and actions(result) == [A.REPLY_NOW]
    assert fakes.tasks.of("reply") == [("reply", chat.id, 0)] and fakes.llm.calls == []
    [seen] = await looks(db, chat)
    assert (seen.actor, seen.in_context) == ("persona", False)
    assert seen.data == {"action": "reply_now", "who": "Марина", "reason": "«Всегда сразу»",
                         "source": "rule", "until_event_id": event.id, "later": None,
                         "later_at": None}
    assert (await fresh(db, persona)).attention_check_at is None


async def test_a_lively_exchange_is_answered_now(db: AsyncSession, deps: Deps, fakes: Fakes,
                                                 clock: FixedClock) -> None:
    persona = await make_persona(db)
    chat = await make_chat(db, persona)
    now = clock.now()
    await add_message(db, chat, "как дела?", at=now - timedelta(seconds=60),
                      direction=Direction.OUT)
    await add_message(db, chat, "норм, а ты?", at=now - timedelta(seconds=20))
    await due_now(db, persona, clock)

    result = await look(deps, persona)

    assert actions(result) == [A.REPLY_NOW] and result.decisions[0].reason == "живая переписка"
    assert fakes.llm.calls == [] and fakes.tasks.of("reply") == [("reply", chat.id, 0)]


async def test_an_edit_of_an_answered_message_is_only_read(db: AsyncSession, deps: Deps,
                                                           fakes: Fakes,
                                                           clock: FixedClock) -> None:
    persona = await make_persona(db)
    chat = await make_chat(db, persona)
    now = clock.now()
    message, _ = await add_message(db, chat, "привет", at=now - timedelta(minutes=10))
    await add_message(db, chat, "привет!", at=now - timedelta(minutes=9),
                      direction=Direction.OUT)
    await add_event(db, chat, EventKind.MESSAGE_EDITED, at=now - timedelta(minutes=1),
                    message=message, data={"old": "привет", "new": "приветик"})
    await due_now(db, persona, clock)

    result = await look(deps, persona)

    assert actions(result) == [A.READ_ONLY] and fakes.llm.calls == []
    assert fakes.tasks.calls == []                      # nothing to answer, nothing to mark


# --- the agent and every action ----------------------------------------------------------------


async def test_busy_she_reads_and_answers_at_the_promised_time(db: AsyncSession, deps: Deps,
                                                               fakes: Fakes,
                                                               clock: FixedClock) -> None:
    persona = await living(db)
    clock.at = utc(SATURDAY, "20:10")                   # talking to her daughter until 20:30
    chat = await make_chat(db, persona)
    message, event = await add_message(db, chat, "привет! как ты?",
                                       at=clock.now() - timedelta(minutes=2))
    fakes.llm.script(ATTENTION_MODEL, plan(decision(chat, "reply_later", later="20:40",
                                                    reason="созвон с дочкой")))
    await due_now(db, persona, clock)

    result = await look(deps, persona)

    [chosen] = result.decisions
    assert (chosen.action, chosen.later_at, chosen.source) == (
        A.REPLY_LATER, utc(SATURDAY, "20:40"), "agent")
    assert fakes.tasks.of("mark_read") == [("mark_read", message.id)]
    assert fakes.tasks.of("reply") == []
    assert fakes.tasks.of("phone_check") == [("phone_check", persona.id, 30 * 60.0)]
    [seen] = await looks(db, chat)
    assert seen.data == {"action": "reply_later", "who": "Марина", "reason": "созвон с дочкой",
                         "source": "agent", "until_event_id": event.id, "later": "20:40",
                         "later_at": utc(SATURDAY, "20:40").isoformat()}
    # What the agent was given: her scene and the chat.
    [call] = fakes.llm.calls_for(ATTENTION_MODEL)
    assert call["key_tier"] == "paid" and call["label"] == "attention"
    assert call["json_schema"] == AttentionPlan.model_json_schema()
    assert "Марина" in (call["messages"][0].parts[0].text or "")
    payload = agent_payload(fakes)
    assert payload["scene"]["activity"] == "созвон с Соней"
    assert payload["scene"]["until"] == "20:30" and payload["scene"]["busy"].startswith("partial")
    [card] = payload["chats"]
    assert (card["chat_id"], card["unanswered"], card["last_from"]) == (chat.id, 1, "client")
    assert "есть вопрос" in card["hints"]
    [logged] = await all_rows(db, select(LlmCall))
    assert logged.purpose == LlmPurpose.ATTENTION and logged.chat_id is None

    # 20:40: the promise is due — she answers without asking the agent again.
    clock.at = utc(SATURDAY, "20:40")
    again = await look(deps, persona)
    assert actions(again) == [A.REPLY_NOW]
    assert again.decisions[0].reason == "обещала ответить в 20:40"
    assert fakes.tasks.of("reply") == [("reply", chat.id, 0)]
    assert len(fakes.llm.calls_for(ATTENTION_MODEL)) == 1


async def test_read_only_and_leave_unread_in_one_look(db: AsyncSession, deps: Deps,
                                                      fakes: Fakes, clock: FixedClock) -> None:
    persona = await make_persona(db)
    ok_chat = await make_chat(db, persona, name="Дима")
    spam_chat = await make_chat(db, persona, name="Спам")
    now = clock.now()
    ok, _ = await add_message(db, ok_chat, "ок, спокойной ночи", at=now - timedelta(minutes=3))
    await add_message(db, spam_chat, "купи крипту", at=now - timedelta(minutes=3))
    fakes.llm.script(ATTENTION_MODEL, plan(decision(ok_chat, "read_only"),
                                           decision(spam_chat, "leave_unread")))
    await due_now(db, persona, clock)

    result = await look(deps, persona)

    assert {d.chat_id: d.action for d in result.decisions} == {
        ok_chat.id: A.READ_ONLY, spam_chat.id: A.LEAVE_UNREAD}
    assert len(fakes.llm.calls_for(ATTENTION_MODEL)) == 1           # one look, one agent call
    assert fakes.tasks.calls == [("mark_read", ok.id)]              # the spam stays unread
    assert [c["chat_id"] for c in agent_payload(fakes)["chats"]] == [ok_chat.id, spam_chat.id]


async def test_a_clear_chat_does_not_wait_for_the_agent(db: AsyncSession, deps: Deps,
                                                       fakes: Fakes,
                                                       clock: FixedClock) -> None:
    """«Отвечать сразу» in one chat starts its turn before the agent is asked about another
    chat (seen in production: the agent hung and the instant chat waited minutes)."""
    persona = await make_persona(db)
    instant = await make_chat(db, persona, name="Дима")
    other = await make_chat(db, persona, name="Max")
    instant.attention_instant = True
    now = clock.now()
    await add_message(db, instant, "пришли кружок", at=now - timedelta(seconds=20))
    await add_message(db, other, "ок", at=now - timedelta(minutes=3))
    seen_when_asked: list[list[Any]] = []

    def agent(record: dict[str, Any]) -> str:
        seen_when_asked.append(list(fakes.tasks.of("reply")))
        return plan(decision(other, "read_only"))

    fakes.llm.script(ATTENTION_MODEL, agent)
    await due_now(db, persona, clock)

    result = await look(deps, persona)

    assert seen_when_asked == [[("reply", instant.id, 0)]]     # already started
    assert {d.chat_id: d.action for d in result.decisions} == {
        instant.id: A.REPLY_NOW, other.id: A.READ_ONLY}
    assert fakes.tasks.of("reply") == [("reply", instant.id, 0)]   # not twice
    assert [c["chat_id"] for c in agent_payload(fakes)["chats"]] == [other.id]


async def test_without_leave_read_she_answers_later_instead(db: AsyncSession, deps: Deps,
                                                            fakes: Fakes,
                                                            clock: FixedClock) -> None:
    persona = await make_persona(db, attention={"may_leave_read": False})
    chat = await make_chat(db, persona)
    await add_message(db, chat, "ок", at=clock.now() - timedelta(minutes=3))
    fakes.llm.script(ATTENTION_MODEL, plan(decision(chat, "read_only")))
    await due_now(db, persona, clock)

    result = await look(deps, persona)

    [chosen] = result.decisions
    assert chosen.action == A.REPLY_LATER
    assert chosen.later_at == clock.now() + timedelta(seconds=20)   # free: after a glance
    assert "read_only" not in agent_payload(fakes)["allowed"]


async def test_a_missed_call_gets_a_text_with_the_note(db: AsyncSession, deps: Deps,
                                                       fakes: Fakes, clock: FixedClock) -> None:
    persona = await make_persona(db)
    chat = await make_chat(db, persona)
    await add_event(db, chat, EventKind.CALL, at=clock.now() - timedelta(minutes=5),
                    data={"status": "missed", "life_busy": "away",
                          "life_activity": "сон, телефон в другой комнате"})
    # Outgoing calls do not exist: «call back» becomes a text after the missed call.
    fakes.llm.script(ATTENTION_MODEL, plan(decision(chat, "call_back", reason="проснулась")))
    await due_now(db, persona, clock)

    result = await look(deps, persona)

    assert actions(result) == [A.TEXT_AFTER_MISSED_CALL]
    assert fakes.tasks.of("reply") == [("reply", chat.id, 0)]
    [seen] = await looks(db, chat)
    note = "пропущенный звонок, ты была занята: сон, телефон в другой комнате"
    assert seen.in_context and seen.data["note"] == note
    [card] = agent_payload(fakes)["chats"]
    assert card["missed_calls"][0]["while"] == "сон, телефон в другой комнате"

    # The reply turn itself is unchanged; it only reads the note in the timeline.
    fakes.llm.script("reply-model", plan_json({"type": "text", "text": "прости, спала"}))
    fakes.llm.script("guard-model", ok_verdict())
    assert (await ReplyService(deps).run(chat.id)).status == RunStatus.SENT
    user = fakes.llm.calls_for("reply-model")[0]["messages"][1].parts[0].text or ""
    assert f"({note})" in user


async def test_no_text_after_a_missed_call_when_turned_off(db: AsyncSession, deps: Deps,
                                                           fakes: Fakes,
                                                           clock: FixedClock) -> None:
    persona = await make_persona(db, attention={"after_missed_call": False})
    chat = await make_chat(db, persona)
    await add_event(db, chat, EventKind.CALL, at=clock.now() - timedelta(minutes=5),
                    data={"status": "missed", "life_busy": "away"})
    await due_now(db, persona, clock)
    assert (await look(deps, persona)).status == CheckStatus.NOTHING


# --- never lose a message ----------------------------------------------------------------------


async def test_an_agent_error_falls_back_to_the_end_of_the_busy_event(
        db: AsyncSession, deps: Deps, fakes: Fakes, clock: FixedClock) -> None:
    persona = await living(db)
    clock.at = utc(SATURDAY, "20:10")
    chat = await make_chat(db, persona)
    message, _ = await add_message(db, chat, "ты тут?", at=clock.now() - timedelta(minutes=1))
    fakes.llm.script(ATTENTION_MODEL, PortError("broker down", retryable=True))
    await due_now(db, persona, clock)

    result = await look(deps, persona)

    [chosen] = result.decisions
    assert (chosen.action, chosen.later_at, chosen.source) == (
        A.REPLY_LATER, utc(SATURDAY, "20:30"), "fallback")
    assert chosen.reason == "«Внимание» не ответило: PortError"
    assert fakes.tasks.of("mark_read") == [("mark_read", message.id)]
    assert fakes.tasks.of("phone_check") == [("phone_check", persona.id, 20 * 60.0)]
    [logged] = await all_rows(db, select(LlmCall))
    assert logged.error == "broker down"


async def test_nonsense_and_a_skipped_chat_fall_back(db: AsyncSession, deps: Deps,
                                                     fakes: Fakes, clock: FixedClock) -> None:
    persona = await make_persona(db)
    first = await make_chat(db, persona, name="Дима")
    second = await make_chat(db, persona, name="Олег")
    for chat in (first, second):
        await add_message(db, chat, "привет", at=clock.now() - timedelta(minutes=3))
    fakes.llm.script(ATTENTION_MODEL, "не знаю, что ответить")
    await due_now(db, persona, clock)

    unreadable = await look(deps, persona)
    assert [(d.action, d.reason) for d in unreadable.decisions] == [
        (A.REPLY_LATER, UNREADABLE), (A.REPLY_LATER, UNREADABLE)]

    # Both promised answers come due (that is the pending check now); a new message in the
    # second chat arrives meanwhile.
    clock.advance(seconds=30)
    await add_message(db, second, "ау?", at=clock.now())
    fakes.llm.script(ATTENTION_MODEL, plan(decision(first, "reply_now")))   # skips «Олег»
    result = await look(deps, persona)
    by_chat = {d.chat_id: d for d in result.decisions}
    assert by_chat[first.id].action == A.REPLY_NOW and by_chat[first.id].source == "rule"
    assert (by_chat[second.id].action, by_chat[second.id].reason) == (A.REPLY_LATER,
                                                                      SKIPPED_CHAT)


async def test_without_the_agent_the_rules_and_the_fallback_decide(
        db: AsyncSession, deps: Deps, fakes: Fakes, clock: FixedClock) -> None:
    persona = await make_persona(db, models={"attention": ""})
    chat = await make_chat(db, persona)
    await add_message(db, chat, "привет", at=clock.now() - timedelta(minutes=3))
    await due_now(db, persona, clock)
    [chosen] = (await look(deps, persona)).decisions
    assert (chosen.action, chosen.reason) == (A.REPLY_LATER, NO_MODEL)
    assert fakes.llm.calls == []


# --- her life ----------------------------------------------------------------------------------


async def test_asleep_she_looks_after_waking_up(db: AsyncSession, deps: Deps, fakes: Fakes,
                                                clock: FixedClock) -> None:
    persona = await living(db)
    clock.at = utc(SUNDAY, "02:00")
    chat = await make_chat(db, persona)
    await add_message(db, chat, "спишь?", at=clock.now() - timedelta(minutes=1))
    await due_now(db, persona, clock)

    result = await look(deps, persona)

    wake = utc(SUNDAY, "06:30") + timedelta(seconds=20)
    assert result.status == CheckStatus.AWAY and result.next_check == wake
    assert fakes.tasks.calls == [("phone_check", persona.id, 4.5 * 3600 + 20.0)]
    assert fakes.llm.calls == [] and await looks(db, chat) == []
    assert (await fresh(db, persona)).attention_check_at == wake


async def test_without_do_not_disturb_she_glances_at_night(db: AsyncSession, deps: Deps,
                                                           fakes: Fakes,
                                                           clock: FixedClock) -> None:
    persona = await living(db, attention={"night_dnd": False})
    clock.at = utc(SUNDAY, "02:00")
    chat = await make_chat(db, persona)
    await add_message(db, chat, "срочно, ответь", at=clock.now() - timedelta(minutes=1))
    fakes.llm.script(ATTENTION_MODEL, plan(decision(chat, "reply_now", reason="срочно")))
    await due_now(db, persona, clock)

    result = await look(deps, persona)

    assert result.status == CheckStatus.DONE and actions(result) == [A.REPLY_NOW]
    payload = agent_payload(fakes)
    assert payload["scene"]["busy"].startswith("partial")
    assert "пишет «срочно»" in payload["chats"][0]["hints"]


# --- «Всегда сразу» in the inbox: exactly the old behaviour --------------------------------------


async def test_always_at_once_replies_after_the_burst_pause(db: AsyncSession, deps: Deps,
                                                            fakes: Fakes,
                                                            clock: FixedClock) -> None:
    persona = await living(db, attention={"mode": "instant"}, reply_delay_s=5)
    clock.at = utc(SUNDAY, "02:00")                     # even asleep
    chat = await make_chat(db, persona, external_id=PEER_ID)
    await Inbox(deps).ingest(incoming(chat, clock, "1"))
    assert fakes.tasks.calls == [("reply", chat.id, 5.0)]
    assert (await fresh(db, persona)).attention_check_at is None


async def test_the_chat_switch_replies_after_the_burst_pause(db: AsyncSession, deps: Deps,
                                                             fakes: Fakes,
                                                             clock: FixedClock) -> None:
    persona = await make_persona(db)
    chat = await make_chat(db, persona, external_id=PEER_ID)
    chat.attention_instant = True
    await db.flush()
    await Inbox(deps).ingest(incoming(chat, clock, "1"))
    assert fakes.tasks.calls == [("reply", chat.id, 8.0)]
