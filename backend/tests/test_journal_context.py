"""The communication journal (service lines of the dialog feed) and what a reply is built from:
the context window in full, the summary of what is older, the journal records of the window."""
from __future__ import annotations

import json
from datetime import timedelta

from alembic import command
from sqlalchemy import select

from sim.adapters.db.models import Client, Dialog, DialogEvent, DialogSummary, Message
from sim.adapters.db.session import transaction
from sim.clock import utcnow
from sim.domain.enums import Author, Delivery, Direction, Stage
from sim.ports.llm import LLMError
from sim.services import journal
from sim.services.app_settings import AppSettingsService
from sim.services.context_window import ContextWindow, estimate_tokens, fit
from sim.services.funnel import FunnelService
from sim.services.journal import CommunicationJournal, Who, changed_lines, event_line
from sim.services.passport import passport_request
from sim.services.prompt import dynamic_context
from sim.services.summary import SUMMARY_WORKFLOW, summary_request
from tests.conftest import OPERATOR, OWNER, decision_json, inbound, login
from tests.test_conversation import run_reply
from tests.test_ingest import ingest
from tests.test_migrations import alembic_config, columns
from tests.test_prompt import STRATEGY, ctx
from tests.test_reply_schedule import at_clock, fri, with_routine

NOW = utcnow()
OLD = NOW - timedelta(days=5)
FRESH = NOW - timedelta(hours=3)


async def the_dialog(container) -> Dialog:
    async with container.factory() as s:
        return (await s.scalars(select(Dialog))).unique().one()


async def events_of(container) -> list[DialogEvent]:
    async with container.factory() as s:
        return list((await s.scalars(select(DialogEvent).order_by(DialogEvent.id))).all())


def message(dialog_id: int, text: str, at, *, client: bool) -> Message:
    return Message(
        dialog_id=dialog_id, direction=Direction.IN if client else Direction.OUT,
        author=Author.CLIENT if client else Author.AI, text=text, delivery=Delivery.SENT,
        created_at=at,
    )


async def seed(container, world, *, old: int = 14, fresh: int = 10, size: int = 0) -> int:
    """A dialog with `old` messages five days back, `fresh` ones three hours back and a new
    client question. `size` pads the fresh messages to that many characters."""
    await ingest(container, world.channel.id, inbound("старое-00", ext="o0", at=OLD))
    dialog_id = (await the_dialog(container)).id
    async with transaction(container.factory) as s:
        for i in range(1, old):
            s.add(message(dialog_id, f"старое-{i:02d}", OLD + timedelta(minutes=i), client=i % 2 == 0))
        for i in range(fresh):
            text = f"свежее-{i:02d}".ljust(size, ".")
            s.add(message(dialog_id, text, FRESH + timedelta(minutes=i), client=i % 2 == 1))
    await ingest(container, world.channel.id,
                 inbound("свежий вопрос", ext="n1", at=NOW - timedelta(minutes=1)))
    return dialog_id


def prompt_of(fake_llm, index: int = -1) -> str:
    return "\n".join(m["content"] for m in fake_llm.gen_calls[index][0])


def summary_payload(fake_llm, index: int = -1) -> dict:
    messages, kwargs = fake_llm.summary_calls[index]
    assert kwargs["workflow"] == SUMMARY_WORKFLOW and [m["role"] for m in messages] == ["system", "user"]
    return json.loads(messages[1]["content"])


# ---- the journal: what is written and how it reads -------------------------------------------
async def test_a_stage_change_is_a_service_line_of_the_dialog_feed(api, container, world):
    await ingest(container, world.channel.id, inbound("Привет", at=NOW - timedelta(minutes=5)))
    dialog = await the_dialog(container)
    h = {"X-CSRF-Token": await login(api, *OPERATOR)}
    moved = await api.post(f"/api/clients/{dialog.client_id}/stage",
                           json={"stage": "interested", "reason": "созвон"}, headers=h)
    assert moved.status_code == 200
    await api.post(f"/api/clients/{dialog.client_id}/stage", json={"status": "paused"}, headers=h)
    detail = (await api.get(f"/api/dialogs/{dialog.id}")).json()
    stage, status = detail["events"]
    assert stage == {
        "id": stage["id"], "kind": "stage", "from": "first_contact", "to": "interested",
        "actor": "operator", "actor_name": "Test", "reason": "созвон",
        "created_at": stage["created_at"],
    }
    # No reason given: the field stays empty, nothing is made up.
    assert (status["kind"], status["from"], status["to"], status["reason"]) == (
        "status", "active", "paused", "")
    # The line belongs to the feed by time: it is not a message and comes after the client's.
    assert [m["text"] for m in detail["messages"]] == ["Привет"]
    assert stage["created_at"] > detail["messages"][0]["created_at"]


async def test_clearing_the_chat_wipes_the_journal_and_leaves_its_own_line(api, container, world):
    await ingest(container, world.channel.id, inbound("Привет"))        # an old message: stale
    dialog = await the_dialog(container)
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    await api.post(f"/api/dialogs/{dialog.id}/ai", json={"paused": True}, headers=h)
    await api.post(f"/api/dialogs/{dialog.id}/ai", json={"paused": True}, headers=h)   # no change
    await api.post(f"/api/dialogs/{dialog.id}/ai",
                   json={"paused": False, "reason": "разобрался сам"}, headers=h)
    async with transaction(container.factory) as s:
        (await s.get(Dialog, dialog.id)).needs_operator = True
    await api.post(f"/api/dialogs/{dialog.id}/resolve", json={"reason": "позвонил"}, headers=h)
    await api.post(f"/api/dialogs/{dialog.id}/resolve", headers=h)                    # nothing to clear
    cleared = await api.delete(f"/api/dialogs/{dialog.id}/messages",
                               params={"reason": "тест с нуля"}, headers=h)
    await api.delete(f"/api/clients/{dialog.client_id}/passport", headers=h)
    # Clearing the chat wipes the journal too: the old lines leave the feed and her context,
    # the wipe itself is the first line of the new journal.
    events = await events_of(container)
    assert [(e.kind, e.from_value, e.to_value, e.actor, e.reason) for e in events] == [
        ("chat_cleared", "", "удалено сообщений: 1", "owner", "тест с нуля"),
        ("passport_cleared", "", "паспорт, потребности, заметки и следующий шаг", "owner", ""),
    ]
    assert {e.actor_name for e in events if e.actor == "owner"} == {"Test"}
    body = cleared.json()
    assert body["messages"] == [] and [e["kind"] for e in body["events"]] == ["chat_cleared"]


async def test_pause_resume_and_resolve_are_journaled_with_who_and_why(api, container, world):
    await ingest(container, world.channel.id, inbound("Привет"))        # an old message: stale
    dialog = await the_dialog(container)
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    await api.post(f"/api/dialogs/{dialog.id}/ai", json={"paused": True}, headers=h)
    await api.post(f"/api/dialogs/{dialog.id}/ai", json={"paused": True}, headers=h)   # no change
    await api.post(f"/api/dialogs/{dialog.id}/ai",
                   json={"paused": False, "reason": "разобрался сам"}, headers=h)
    async with transaction(container.factory) as s:
        (await s.get(Dialog, dialog.id)).needs_operator = True
    await api.post(f"/api/dialogs/{dialog.id}/resolve", json={"reason": "позвонил"}, headers=h)
    await api.post(f"/api/dialogs/{dialog.id}/resolve", headers=h)                    # nothing to clear
    events = await events_of(container)
    assert [(e.kind, e.from_value, e.to_value, e.actor, e.reason) for e in events] == [
        ("ai_pause", "on", "paused", "owner", ""),
        ("ai_pause", "paused", "on", "owner", "разобрался сам"),
        ("reply_delay", "", "ответ не создан, сообщения без ответа (1) оставлены оператору",
         "system", "сообщения старше окна ответа после паузы ИИ"),
        ("resolved", "", "", "owner", "позвонил"),
    ]


async def test_an_operator_message_and_a_hand_edit_of_the_card_are_journaled(api, container, world):
    await ingest(container, world.channel.id, inbound("Привет", at=NOW - timedelta(minutes=5)))
    dialog = await the_dialog(container)
    h = {"X-CSRF-Token": await login(api, *OPERATOR)}
    await api.post(f"/api/dialogs/{dialog.id}/messages", json={"text": "Отвечу я"}, headers=h)
    await api.post(f"/api/dialogs/{dialog.id}/messages", json={"text": "И ещё"}, headers=h)
    await api.patch(f"/api/clients/{dialog.client_id}",
                    json={"notes": "VIP", "display_name": "Анна П."}, headers=h)
    await api.patch(f"/api/clients/{dialog.client_id}", json={"notes": "VIP"}, headers=h)
    events = await events_of(container)
    assert [(e.kind, e.from_value, e.to_value, e.actor, e.reason) for e in events] == [
        ("notes", "", "VIP", "operator", ""),
    ]


async def test_model_decisions_are_journaled_as_the_model(container, world, fake_llm):
    await ingest(container, world.channel.id, inbound("Хочу на курс", at=NOW - timedelta(minutes=2)))
    fake_llm.replies = [decision_json(stage="interested", needs=["карьера"], handoff_to_operator=True)]
    assert (await run_reply(container)).status == "sent"
    events = await events_of(container)
    assert [(e.kind, e.from_value, e.to_value, e.actor, e.reason) for e in events] == [
        ("needs", "", "карьера", "model", ""),
        ("stage", "first_contact", "interested", "model", ""),
        ("handoff", "", "operator", "model", "Нужен специалист"),
    ]
    fake_llm.passport_replies = ["Кто он: Анна.\nЦели: карьера."]
    async with transaction(container.factory) as s:
        assert await container.passport(s).update((await the_dialog(container)).id)
    passport = (await events_of(container))[-1]
    assert (passport.kind, passport.actor, passport.actor_name) == ("passport", "model", "паспортистка")
    assert passport.to_value == "Кто он: Анна. / Цели: карьера."


async def test_a_reply_put_off_by_her_routine_and_the_operator_overrides_are_journaled(
    api, container, world,
):
    await with_routine(container, world)
    at_clock(container, fri(9, 10))                    # she is at work until 13:00
    await ingest(container, world.channel.id, inbound("Добрый день", ext="1", at=fri(9, 10)))
    at_clock(container, fri(9, 20))                    # one more message of the same wait: no new line
    await ingest(container, world.channel.id, inbound("Вы тут?", ext="2", at=fri(9, 20)))
    dialog = await the_dialog(container)
    h = {"X-CSRF-Token": await login(api, *OPERATOR)}
    await api.post(f"/api/dialogs/{dialog.id}/urgent", json={"urgent": True}, headers=h)
    await api.post(f"/api/dialogs/{dialog.id}/urgent", json={"urgent": True}, headers=h)   # no change
    await api.post(f"/api/dialogs/{dialog.id}/reply-now", headers=h)
    events = await events_of(container)
    assert [(e.kind, e.from_value, e.to_value, e.actor, e.reason) for e in events] == [
        ("reply_delay", "", "reply_at:2026-11-13T09:40:00Z", "system",
         "на работе: консультации с клиентами до 13:00 (распорядок: UTC)"),
        ("urgent", "обычный", "срочный", "operator", ""),
        ("reply_delay", "", "ответить сейчас, не дожидаясь распорядка", "operator", ""),
    ]


async def test_a_rejected_reply_hands_the_dialog_over_with_the_reason(container, world, fake_llm):
    await ingest(container, world.channel.id, inbound("Привет", at=NOW - timedelta(minutes=2)))
    fake_llm.replies = ["not json", "still not json", "no", "no"]
    assert (await run_reply(container)).status == "needs_operator"
    (handoff,) = await events_of(container)
    assert (handoff.kind, handoff.to_value, handoff.actor) == ("handoff", "operator", "system")
    assert handoff.reason.startswith("модель не дала ответа, прошедшего проверку")


async def test_a_new_persona_model_and_strategy_text_reach_her_live_dialogs(api, container, world):
    await ingest(container, world.channel.id, inbound("Привет", at=NOW - timedelta(hours=1)))
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    persona = {**(await api.get(f"/api/personas/{world.persona.id}")).json(), "gender": "female"}
    saved = await api.put(f"/api/personas/{world.persona.id}",
                          json={**persona, "model": "gemini/flash"}, headers=h)
    assert saved.status_code == 200, saved.text
    await api.put(f"/api/strategies/{world.strategy.id}", json={"name": "S", "offer": "Новый курс"},
                  headers=h)
    events = await events_of(container)
    assert [(e.kind, e.from_value, e.to_value, e.actor) for e in events] == [
        ("model", "маршрут по умолчанию", "gemini/flash", "owner"),
        ("strategy", "", "изменено: оффер", "owner"),
    ]
    # A dialog silent for longer than the fan-out window gets no line.
    async with transaction(container.factory) as s:
        (await s.get(Dialog, events[0].dialog_id)).last_message_at = NOW - timedelta(days=90)
    await api.put(f"/api/personas/{world.persona.id}", json={**persona, "model": ""}, headers=h)
    assert len(await events_of(container)) == 2


def test_a_journal_line_reads_what_who_and_why():
    event = DialogEvent(kind="stage", from_value="engaged", to_value="interested", actor="operator",
                        actor_name="Оля", reason="созвон", created_at=NOW.replace(hour=10, minute=5))
    stamp = event.created_at.strftime("%Y-%m-%d %H:%M")
    assert event_line(event) == (
        f"- {stamp} смена этапа: engaged → interested; кто: оператор Оля; причина: созвон")
    silent = DialogEvent(kind="ai_pause", from_value="on", to_value="paused", actor="owner",
                         actor_name="", reason="", created_at=event.created_at)
    assert event_line(silent) == (
        f"- {stamp} ИИ в диалоге: включён → на паузе; кто: владелец; причина: не указана")
    assert changed_lines("а\nб", "б\nв") == ("а", "в")


async def test_reply_keeps_conversation_facts_and_sent_words_not_operational_audit(
    container, world, fake_llm,
):
    await ingest(container, world.channel.id, inbound("Первый вопрос", at=NOW - timedelta(minutes=3)))
    dialog = await the_dialog(container)
    said = "Я уже передала ваш вопрос специалисту"
    boilerplate = "Передача оператору и ответ человека не подтверждены; медиа не отправлены."
    passport = f"Собеседник изучает язык.\n- {boilerplate}"
    technical_step = "Передать оператору просьбу клиента о кружочке."
    ordinary_step = "Уточнить удобное время занятия."
    fake_llm.replies = [decision_json(next_step="")]
    async with transaction(container.factory) as s:
        log = CommunicationJournal(s)
        await log.record(dialog.id, journal.NOTES, new="до очистки")
        await log.record(dialog.id, journal.CHAT_CLEARED, who=Who("owner", "Synthetic owner"))
        client = await s.get(Client, dialog.client_id)
        client.passport = passport
        client.next_step = technical_step
        await log.record(dialog.id, journal.NEXT_STEP, new=technical_step)
        await log.record(dialog.id, journal.NEXT_STEP, new=ordinary_step)
        await FunnelService(s).operator_update(
            client, stage=Stage.INTERESTED, status=None, reason="internal stage note",
            who=Who("operator", "Synthetic operator"),
        )
        for kind in (journal.HANDOFF, journal.AI_PAUSE, journal.MODEL, journal.CALL_ISSUE):
            await log.record(dialog.id, kind, new="internal audit marker", reason=kind)
        s.add(message(dialog.id, said, NOW - timedelta(minutes=2), client=False))
    await ingest(container, world.channel.id, inbound(
        "Где посмотреть материалы?", ext="new-question", at=NOW - timedelta(minutes=1),
    ))
    await run_reply(container)
    prompt = prompt_of(fake_llm)
    assert "ЖУРНАЛА КОММУНИКАЦИИ" in prompt and "смена этапа: interested" in prompt
    assert "Собеседник изучает язык." in prompt and ordinary_step in prompt
    assert technical_step not in prompt
    assert not any(item in prompt for item in (
        "до очистки", "чат очищен", "internal audit marker", "internal stage note",
        "Synthetic operator", boilerplate,
    ))
    turns = "\n".join(m["content"] for m in fake_llm.gen_calls[-1][0][1:-1])
    assert said in turns and "Где посмотреть материалы?" in turns
    events = await events_of(container)
    assert {journal.HANDOFF, journal.AI_PAUSE, journal.MODEL, journal.CALL_ISSUE}.issubset(
        e.kind for e in events
    )
    assert any(e.kind == journal.NEXT_STEP and e.to_value == technical_step for e in events)
    async with container.factory() as s:
        client = await s.get(Client, dialog.client_id)
        assert client.passport == passport and client.next_step == technical_step


def test_operational_sentence_suffix_is_removed_without_losing_earlier_facts_or_headings():
    tail = "Фактическая передача и ответ человека не подтверждены; фото и кружочек в диалоге не отправлены."
    facts = ("Собеседник спросил, когда ему ответит оператор. "
             "Персона предложила передать вопрос специалисту.")
    heading = "Как общается: задаёт короткие вопросы."
    memo = f"{facts} {tail}\n\n{heading}"
    clean = f"{facts}\n\n{heading}"
    projected = dynamic_context(ctx("Synthetic", "r1", summary=memo, passport=memo), STRATEGY)
    assert facts in projected and heading in projected and tail not in projected
    assert json.loads(passport_request("Synthetic", memo, [])[0]["content"])["passport"] == clean
    assert json.loads(summary_request("Synthetic", memo, [])[0]["content"])["summary"] == clean
    marked = f"{facts}\n- {tail}"
    assert json.loads(passport_request("Synthetic", marked, [])[0]["content"])["passport"] == facts
    # Explicitly quoted participant words, rather than an unquoted audit sentence, are not removed.
    quoted = f"Собеседник процитировал сообщение: «{tail}»"
    assert json.loads(passport_request("Synthetic", quoted, [])[0]["content"])["passport"] == quoted


def test_next_step_projection_removes_only_standalone_operational_assignments():
    for step in ("Передать оператору просьбу прислать кружок.", "Перевести диалог на паузу."):
        assert step not in dynamic_context(ctx("Synthetic", "r1", next_step=step), STRATEGY)
    for step in (
        "Уточнить срок и расписание занятия.",
        "Передать оператору просьбу; уточнить дату занятия.",
        "Передать оператору просьбу и уточнить дату занятия.",
        "Собеседник процитировал: «Передать оператору просьбу».",
        "Обсудить, когда оператор подтвердит расписание.",
    ):
        assert step in dynamic_context(ctx("Synthetic", "r1", next_step=step), STRATEGY)


# ---- the context window ----------------------------------------------------------------------
def test_the_token_limit_cuts_the_oldest_and_always_keeps_the_newest():
    rows = [message(1, "я" * 30, NOW, client=True) for _ in range(5)]
    cost = estimate_tokens("я" * 30)
    assert cost == 14
    kept, trimmed, tokens = fit(rows, cost * 3)
    assert kept == rows[-3:] and trimmed == 2 and tokens == cost * 3
    assert fit(rows, 1) == (rows[-1:], 4, cost)
    assert fit([], 100) == ([], 0, 0)


async def test_the_window_is_two_days_with_a_floor_for_a_long_silence(container, world):
    dialog_id = await seed(container, world)
    async with container.factory() as s:
        window = await ContextWindow(s).load(dialog_id, hours=48, token_limit=6000)
        assert [m.text for m in window.messages] == [
            *(f"свежее-{i:02d}" for i in range(10)), "свежий вопрос"]
        assert window.trimmed == 0 and window.note() == ""
        # Nine days later nothing is inside the window: the newest messages are still read.
        later = ContextWindow(s, clock=lambda: NOW + timedelta(days=9))
        floor = await later.load(dialog_id, hours=48, token_limit=6000, min_messages=4)
        assert [m.text for m in floor.messages] == [
            "свежее-07", "свежее-08", "свежее-09", "свежий вопрос"]
        assert floor.trimmed == 0


async def test_a_reply_reads_two_days_in_full_and_the_summary_of_the_rest(container, world, fake_llm):
    dialog_id = await seed(container, world)
    fake_llm.summary_replies = ["Саммари: обсуждали курс, Анна сомневалась в цене."]
    assert (await run_reply(container)).status == "sent"
    turns = "\n".join(m["content"] for m in fake_llm.gen_calls[-1][0][1:-1])
    assert all(f"свежее-{i:02d}" in turns for i in range(10)) and "свежий вопрос" in turns
    assert "старое-" not in prompt_of(fake_llm)
    context = fake_llm.gen_calls[-1][0][-1]["content"]
    assert "САММАРИ БОЛЕЕ РАННЕЙ ПЕРЕПИСКИ" in context and "сомневалась в цене" in context
    assert "ИСТОРИЯ ОБРЕЗАНА" not in context
    # The summary agent got exactly the messages older than the window, as JSON data.
    payload = summary_payload(fake_llm)
    assert payload["summary"] == "" and payload["persona"] == "Марина"
    assert [t["text"] for t in payload["dialog"]] == [f"старое-{i:02d}" for i in range(14)]
    assert {t["role"] for t in payload["dialog"]} == {"client", "persona"}
    async with container.factory() as s:
        row = await s.get(DialogSummary, dialog_id)
        last_old = await s.scalar(select(Message).where(Message.text == "старое-13"))
    assert row.covered_message_id == last_old.id and row.covered_until == last_old.created_at
    assert row.messages_covered == 14 and row.model == "fake-summary"


async def test_the_summary_is_kept_and_extended_not_rebuilt(container, world, fake_llm):
    dialog_id = await seed(container, world)
    fake_llm.summary_replies = ["Саммари-1"]
    await run_reply(container)
    await ingest(container, world.channel.id,
                 inbound("ещё вопрос", ext="n2", at=NOW - timedelta(seconds=20)))
    await run_reply(container)
    assert len(fake_llm.summary_calls) == 1          # nothing new became old: no model call
    assert "Саммари-1" in prompt_of(fake_llm)
    # Three days later the fresh messages are old: only they are added to the kept summary.
    container.clock = lambda: NOW + timedelta(days=3)
    fake_llm.summary_replies = ["Саммари-2"]
    await ingest(container, world.channel.id,
                 inbound("я вернулась", ext="n3", at=NOW + timedelta(days=3, minutes=-1)))
    await run_reply(container)
    payload = summary_payload(fake_llm)
    assert payload["summary"] == "Саммари-1"
    texts = [t["text"] for t in payload["dialog"]]
    assert texts and texts[0] == "свежее-00" and not any(t.startswith("старое") for t in texts)
    assert "Саммари-2" in prompt_of(fake_llm) and "Саммари-1" not in prompt_of(fake_llm)
    async with container.factory() as s:
        row = await s.get(DialogSummary, dialog_id)
    assert row.text == "Саммари-2" and row.messages_covered == 14 + len(texts)


async def test_over_the_token_limit_the_oldest_of_the_window_is_cut_and_summarised(
    container, world, fake_llm,
):
    await seed(container, world, size=300)
    async with transaction(container.factory) as s:
        await AppSettingsService(s).set("context_token_limit", 500, None)
    await run_reply(container)
    turns = "\n".join(m["content"] for m in fake_llm.gen_calls[-1][0][1:-1])
    assert "свежий вопрос" in turns and "свежее-09" in turns
    assert "свежее-06" in turns and "свежее-00" not in turns and "свежее-05" not in turns
    context = fake_llm.gen_calls[-1][0][-1]["content"]
    assert "ИСТОРИЯ ОБРЕЗАНА: за последние 48 ч" in context
    assert "6 самых старых из них в историю выше не вошли" in context
    # What was cut is not lost: the summary covers it together with the older messages.
    texts = [t["text"][:9] for t in summary_payload(fake_llm)["dialog"]]
    assert texts[:14] == [f"старое-{i:02d}" for i in range(14)]
    assert texts[14:] == [f"свежее-{i:02d}" for i in range(6)]


async def test_a_reply_does_not_wait_for_a_summary_that_cannot_be_made(container, world, fake_llm):
    await seed(container, world)
    fake_llm.summary_replies = [LLMError("broker is down")]
    assert (await run_reply(container)).status == "sent"
    assert "САММАРИ" not in prompt_of(fake_llm) and "свежее-00" in prompt_of(fake_llm)


def test_the_context_names_the_coverage_of_the_summary_and_skips_empty_memory():
    plain = dynamic_context(ctx("Анна", "r1"), STRATEGY)
    assert "САММАРИ" not in plain and "ЖУРНАЛ" not in plain and "ОБРЕЗАНА" not in plain
    until = NOW.replace(hour=9, minute=30)
    text = dynamic_context(
        ctx("Анна", "r1", summary="говорили о цене", summary_until=until, journal=["- строка"]),
        STRATEGY)
    assert f"охватывает переписку до {until.strftime('%Y-%m-%d')} 09:30 UTC" in text
    assert "говорили о цене" in text and "- строка" in text


# ---- the summary in the dialog card ----------------------------------------------------------
async def test_the_card_shows_the_summary_and_the_button_extends_it(api, container, world, fake_llm):
    dialog_id = await seed(container, world)
    h = {"X-CSRF-Token": await login(api, *OPERATOR)}
    empty = (await api.get(f"/api/dialogs/{dialog_id}/summary")).json()
    assert empty == {"text": "", "covered_until": None, "messages_covered": 0, "updated_at": None,
                     "model": None, "pending": 14}
    # The model is down: 502 and nothing is saved; the next press does the work.
    fake_llm.summary_replies = [LLMError("broker is down"), "Саммари из кнопки"]
    assert (await api.post(f"/api/dialogs/{dialog_id}/summary", headers=h)).status_code == 502
    assert (await api.get(f"/api/dialogs/{dialog_id}/summary")).json() == empty
    made = (await api.post(f"/api/dialogs/{dialog_id}/summary", headers=h)).json()
    assert made["text"] == "Саммари из кнопки" and made["messages_covered"] == 14
    assert made["pending"] == 0 and made["covered_until"] and made["updated_at"]
    assert (await api.get(f"/api/dialogs/{dialog_id}/summary")).json() == made
    again = (await api.post(f"/api/dialogs/{dialog_id}/summary", headers=h)).json()
    assert again == made and len(fake_llm.summary_calls) == 2        # nothing new to fold in
    # «Очистить чат» removes the summary together with the messages it was made of.
    owner = {"X-CSRF-Token": await login(api, *OWNER)}
    await api.delete(f"/api/dialogs/{dialog_id}/messages", headers=owner)
    assert (await api.get(f"/api/dialogs/{dialog_id}/summary")).json()["text"] == ""
    assert (await api.get("/api/dialogs/999999/summary")).status_code == 404


async def test_the_agent_settings_list_the_summary_and_the_window(api):
    await login(api, *OWNER)
    items = {i["key"]: i for i in (await api.get("/api/settings")).json()["items"]}
    assert items["summary_prompt"]["group"] == "Саммари диалога" and items["summary_model"]["kind"] == "model"
    assert (items["context_window_hours"]["default"], items["context_token_limit"]["default"]) == (48, 6000)


def test_0026_adds_the_journal_and_summary_tables_and_roundtrips(tmp_path):
    db = tmp_path / "mig.db"
    cfg = alembic_config(db)
    command.upgrade(cfg, "head")
    assert {"dialog_id", "kind", "from_value", "to_value", "actor", "actor_name", "reason",
            "created_at"} <= columns(db, "dialog_event")
    assert {"dialog_id", "text", "covered_message_id", "covered_until", "messages_covered",
            "updated_at"} <= columns(db, "dialog_summary")
    command.downgrade(cfg, "0025")
    assert not columns(db, "dialog_event") and not columns(db, "dialog_summary")
