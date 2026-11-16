"""Synthetic dialogs only. Real providers are never used."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from sim.adapters.db.models import Dialog, Job, Message
from sim.adapters.db.session import transaction
from sim.domain.enums import Author, ClientStatus, Delivery, Direction
from sim.services import jobkinds
from sim.services.dialog_state import apply_message
from sim.services.guard import GuardError
from sim.services.reengagement import ReengagementService, planned_time
from sim.worker.handlers import run_send
from sim.worker.loop import Worker
from tests.conftest import OWNER, decision_json, inbound, login
from tests.test_ingest import ingest

NOW = datetime(2026, 11, 16, 12)


async def setup_dialog(c, world):
    c.clock = lambda: NOW
    got = await ingest(c, world.channel.id, inbound("Синтетическая тема", at=NOW - timedelta(minutes=6)))
    async with transaction(c.factory) as s:
        d = await s.get(Dialog, got.dialog.id)
        d.client.passport = "Синтетический интерес: садоводство"
        m = Message(dialog_id=d.id, direction=Direction.OUT, author=Author.AI, text="Поговорим позже",
                    delivery=Delivery.SENT, created_at=NOW - timedelta(minutes=6))
        s.add(m)
        await s.flush()
        apply_message(d, m)
        for j in await s.scalars(select(Job)):
            j.status = "done"
    return got.dialog.id


async def sweep(c):
    async with transaction(c.factory) as s:
        return await ReengagementService(s, clock=c.clock).sweep()


@pytest.mark.parametrize("corrected", ["Как растут цветы?", ""])
async def test_guard_feedback_regenerates_full_plan(container, world, fake_llm, connector, corrected):
    from tests.conftest import guard_json

    did = await setup_dialog(container, world)
    fake_llm.replies = [
        decision_json(reply="Гарантированный доход", plan_reason="Ошибка", send_at="2026-11-16T13:00:00Z",
                      handoff_to_operator=True, handoff_reason="Неверная передача"),
        decision_json(reply=corrected, plan_reason="Исправленный план",
                      send_at="2026-11-16T14:00:00Z" if corrected else None),
    ]
    fake_llm.guard_replies = [guard_json("rewrite", ["Неверное обещание и передача"], "Не отправлять"),
                              guard_json("ok")]
    assert await sweep(container) == 1
    await Worker(container).tick()
    assert len(fake_llm.gen_calls) == 2
    assert "Неверное обещание и передача" in fake_llm.gen_calls[1][0][-1]["content"]
    async with container.factory() as s:
        d = await s.get(Dialog, did)
        assert not d.ai_paused and not d.needs_operator
        assert d.reengagement_plan["text"] == corrected
        assert d.reengagement_plan["reason"] == "Исправленный план"
        assert d.reengagement_plan["at"] == ("2026-11-16T14:00:00Z" if corrected else None)
        assert d.reengagement_plan["status"] == ("scheduled" if corrected else "no_send")
    assert connector.port.sent == []


async def plan(c, world, llm):
    did = await setup_dialog(c, world)
    llm.replies = [decision_json(reply="Как растут цветы?", plan_reason="Релевантная тема паспорта",
                                 send_at="2026-11-16T13:00:00Z")]
    assert await sweep(c) == 1
    await Worker(c).tick()
    async with c.factory() as s:
        d = await s.get(Dialog, did)
        assert d.reengagement_plan["status"] == "scheduled"
        j = await s.scalar(select(Job).where(Job.kind == jobkinds.SEND))
        return did, dict(j.payload)


@pytest.mark.parametrize("second_reply", ["", "Ещё одно напоминание"])
async def test_delivery_reassessed_after_silence_without_spam(container, world, fake_llm, connector, second_reply):
    did, payload = await plan(container, world, fake_llm)
    assert connector.port.sent == []
    assert await sweep(container) == 0
    async with container.factory() as s:
        d = await s.get(Dialog, did)
        assert d.reengagement_plan["text"] == "Как растут цветы?"
        assert d.reengagement_plan["at"] == "2026-11-16T13:00:00Z"
    prompt = "\n".join(m["content"] for m in fake_llm.gen_calls[0][0])
    assert "Синтетическая тема" in prompt and "садоводство" in prompt
    container.clock = lambda: NOW + timedelta(hours=1)
    async with transaction(container.factory) as s:
        await run_send(container, s, payload)
    async with transaction(container.factory) as s:
        await run_send(container, s, payload)
    assert connector.port.sent == [("chat-1", "Как растут цветы?")]
    async with container.factory() as s:
        d = await s.get(Dialog, did)
        assert d.last_message_text == "Как растут цветы?"
        assert d.last_message_at == NOW + timedelta(hours=1)
        assert d.reengagement_plan["delivered_at"] == "2026-11-16T13:00:00Z"
        assert d.activity_revision > d.reengagement_plan["revision"]
    container.clock = lambda: NOW + timedelta(hours=1, minutes=4, seconds=59)
    assert await sweep(container) == 0
    container.clock = lambda: NOW + timedelta(hours=1, minutes=5)
    fake_llm.replies = [decision_json(reply=second_reply, plan_reason="Ждём ответа",
                                    send_at="2026-11-16T14:00:00Z" if second_reply else None)]
    assert await sweep(container) == 1
    await Worker(container).tick()
    assert len(fake_llm.gen_calls) == 2
    async with container.factory() as s:
        d = await s.get(Dialog, did)
        assert d.reengagement_plan["status"] == "no_send"
        assert d.reengagement_plan["last_sent_id"] == payload["message_id"]
    assert await sweep(container) == 0  # assessment is coalesced, no model/transport loop
    assert connector.port.sent == [("chat-1", "Как растут цветы?")]


@pytest.mark.parametrize("flag", ["handoff_to_operator", "stop_contact", "refused"])
@pytest.mark.parametrize("verdict", ["ok", "block", "rewrite", "superseded"])
async def test_empty_control_decision_guarded_without_send(container, world, fake_llm, connector, flag, verdict):
    from tests.conftest import guard_json

    did = await setup_dialog(container, world)
    fake_llm.replies = [decision_json(reply="", plan_reason="Контекст требует действия",
        send_at=None, handoff_reason="Требуется специалист", **{flag: True})]
    if verdict in ("block", "rewrite"):
        fake_llm.replies *= 2
        fake_llm.guard_replies = [guard_json(verdict, ["Проверка решения"], "Неожиданный текст")] * 2
    elif verdict == "superseded":
        original = fake_llm.chat

        async def interleave(messages, **kw):
            if kw.get("workflow") == "guard":
                await ingest(container, world.channel.id, inbound("Новый контекст", ext="new", at=NOW))
            return await original(messages, **kw)

        fake_llm.chat = interleave
    assert await sweep(container) == 1
    await Worker(container).tick()
    assert fake_llm.guard_calls
    data = json.loads(fake_llm.guard_calls[0][0][1]["content"])
    assert data["decision"][flag] is True
    async with container.factory() as s:
        d = await s.get(Dialog, did)
        applied = verdict == "ok"
        assert d.needs_operator is (applied and flag == "handoff_to_operator")
        assert d.ai_paused is (applied and flag == "handoff_to_operator")
        assert d.client.no_followups is (applied and flag == "stop_contact")
        assert (d.client.status is ClientStatus.REFUSED) is (applied and flag != "handoff_to_operator")
        if applied and flag == "handoff_to_operator":
            trigger = await s.scalar(select(Message).where(Message.dialog_id == did, Message.direction == Direction.IN))
            assert trigger.handoff_reason == "Требуется специалист"
        assert not (await s.scalars(select(Message).where(
            Message.dialog_id == did, Message.delivery == Delivery.QUEUED))).all()
    assert connector.port.sent == []


@pytest.mark.parametrize("change", ["inbound", "phone", "pause", "optout", "status", "passport"])
async def test_context_change_fences_stale_send(container, world, fake_llm, connector, change):
    did, payload = await plan(container, world, fake_llm)
    container.clock = lambda: NOW + timedelta(minutes=30)
    if change in ("inbound", "phone"):
        await ingest(container, world.channel.id, inbound("Новый контекст", ext="new",
            at=container.clock(), from_me=change == "phone"))
    else:
        async with transaction(container.factory) as s:
            d = await s.get(Dialog, did)
            if change == "pause":
                d.ai_paused = True
            elif change == "optout":
                d.client.no_followups = True
            elif change == "status":
                d.client.status = ClientStatus.REFUSED
            else:
                d.client.passport = "Обновлённые факты"
    container.clock = lambda: NOW + timedelta(hours=1)
    async with transaction(container.factory) as s:
        await run_send(container, s, payload)
    assert connector.port.sent == []


async def test_new_message_during_analysis_supersedes_plan(container, world, fake_llm, connector):
    did = await setup_dialog(container, world)
    original = fake_llm.chat

    async def interleave(messages, **kw):
        if kw.get("workflow") == "reengagement":
            await ingest(container, world.channel.id, inbound("Продолжим", ext="new", at=NOW))
        return await original(messages, **kw)

    fake_llm.chat = interleave
    fake_llm.replies = [decision_json(reply="Старый план", plan_reason="Тема", send_at="2026-11-16T13:00:00Z")]
    await sweep(container)
    await Worker(container).tick()
    async with container.factory() as s:
        assert (await s.get(Dialog, did)).reengagement_plan["status"] == "outdated"
        assert not (await s.scalars(select(Message).where(Message.text == "Старый план"))).all()
    assert connector.port.sent == []


async def test_threshold_and_no_send_decision(container, world, fake_llm):
    did = await setup_dialog(container, world)
    container.clock = lambda: NOW - timedelta(minutes=2)
    assert await sweep(container) == 0
    container.clock = lambda: NOW
    fake_llm.replies = [decision_json(reply="", send_at=None, plan_reason="Неуместно беспокоить")]
    assert await sweep(container) == 1
    await Worker(container).tick()
    assert await sweep(container) == 0
    async with container.factory() as s:
        assert (await s.get(Dialog, did)).reengagement_plan["status"] == "no_send"


async def test_message_during_final_review_blocks_transport(container, world, fake_llm, connector):
    did, payload = await plan(container, world, fake_llm)
    original = fake_llm.chat
    container.clock = lambda: NOW + timedelta(hours=1)

    async def interleave(messages, **kw):
        if kw.get("workflow") == "guard":
            await ingest(container, world.channel.id, inbound("Новый вопрос", ext="during-send", at=container.clock()))
        return await original(messages, **kw)

    fake_llm.chat = interleave
    async with transaction(container.factory) as s:
        await run_send(container, s, payload)
    assert connector.port.sent == []
    async with container.factory() as s:
        assert (await s.get(Dialog, did)).reengagement_plan["status"] == "outdated"


async def test_uncertain_send_does_not_retry_transport(container, world, fake_llm, connector):
    from sim.services.send_receipts import SendUncertain

    _, payload = await plan(container, world, fake_llm)
    container.clock = lambda: NOW + timedelta(hours=1)
    calls = []

    async def uncertain(thread, text):
        calls.append((thread, text))
        raise RuntimeError("response lost")

    connector.port.send_text = uncertain
    for _ in range(2):
        with pytest.raises(SendUncertain):
            async with transaction(container.factory) as s:
                await run_send(container, s, payload)
    assert len(calls) == 1


async def test_late_send_is_replanned_not_sent(container, world, fake_llm, connector):
    did, payload = await plan(container, world, fake_llm)
    container.clock = lambda: NOW + timedelta(hours=2)
    async with transaction(container.factory) as s:
        await run_send(container, s, payload)
    assert connector.port.sent == []
    assert await sweep(container) == 1


async def test_operator_message_preserves_explicit_pause_and_handoff(container, world):
    did = await setup_dialog(container, world)
    async with transaction(container.factory) as s:
        d = await s.get(Dialog, did)
        d.ai_paused, d.needs_operator = True, True
        await container.outbound(s).send_manual(d, 1, "Человек ответил")
        assert d.ai_paused and d.needs_operator


async def test_handoff_mark_survives_resume(api, container, world, fake_llm):
    container.clock = lambda: NOW
    got = await ingest(container, world.channel.id, inbound("Нужен специалист", at=NOW))
    fake_llm.replies = [decision_json(handoff_to_operator=True, handoff_reason="Нужен специалист")]
    async with transaction(container.factory) as s:
        await container.conversation(s).run_reply(got.dialog.id)
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    resumed = await api.post(f"/api/dialogs/{got.dialog.id}/ai", json={"paused": False}, headers=h)
    assert not resumed.json()["ai_paused"] and not resumed.json()["needs_operator"]
    detail = (await api.get(f"/api/dialogs/{got.dialog.id}")).json()
    assert detail["messages"][0]["handoff_reason"] == "Нужен специалист"
    assert detail["messages"][0]["handoff_at"]


@pytest.mark.parametrize("value", ["2026-11-16T13:00:00", "yesterday", "2026-11-15T13:00:00Z", "2027-01-01T00:00:00Z"])
def test_plan_time_requires_offset_and_valid_horizon(value):
    with pytest.raises(GuardError):
        planned_time(value, NOW)


@pytest.mark.parametrize(("utc", "tz", "expected"), [
    (datetime(2026, 1, 1, 0, 30), "America/New_York", "2025-12-31T19:30:00-05:00"),
    (datetime(2026, 3, 29, 0, 30), "Europe/Berlin", "2026-03-29T01:30:00+01:00"),
    (datetime(2026, 3, 29, 1, 30), "Europe/Berlin", "2026-03-29T03:30:00+02:00"),
    (datetime(2026, 10, 25, 0, 30), "Europe/Berlin", "2026-10-25T02:30:00+02:00"),
    (datetime(2026, 10, 25, 1, 30), "Europe/Berlin", "2026-10-25T02:30:00+01:00"),
])
def test_clock_date_and_dst_boundaries(utc, tz, expected):
    from sim.services.persona_clock import local_time

    assert local_time(tz, utc).isoformat() == expected


async def test_phone_media_without_caption_updates_context_without_pause(container, world):
    did = await setup_dialog(container, world)
    got = await ingest(container, world.channel.id, inbound("", ext="photo", from_me=True,
        media_kind="photo", at=NOW))
    assert got.message.author is Author.OPERATOR and got.message.media_kind == "photo"
    async with container.factory() as s:
        assert not (await s.get(Dialog, did)).ai_paused


async def test_guard_receives_code_time_and_context_on_rewrite(container, world, fake_llm):
    from tests.conftest import guard_json

    container.clock = lambda: datetime(2026, 12, 31, 23, 30)
    async with transaction(container.factory) as s:
        p = await s.get(type(world.persona), world.persona.id)
        p.timezone = "Europe/Berlin"
    fake_llm.guard_replies = [guard_json("rewrite", ["неверная дата"], "С новым годом"), guard_json()]
    async with transaction(container.factory) as s:
        p = await s.get(type(world.persona), world.persona.id)
        await container.conversation(s).preview(p, "Какой сегодня день?")
    payloads = [json.loads(messages[1]["content"]) for messages, _ in fake_llm.guard_calls]
    assert len(payloads) == 2
    for data in payloads:
        assert data["trusted_facts"]["now_utc"] == "2026-12-31T23:30:00Z"
        assert "1 января 2027" in data["trusted_facts"]["persona_facts"]
        assert "Europe/Berlin" in data["trusted_facts"]["persona_facts"]
        assert "пятница" in data["trusted_facts"]["persona_facts"]


async def test_guard_refreshes_date_after_generation_crosses_midnight(container, world, fake_llm):
    original = fake_llm.chat
    clock = [datetime(2026, 12, 31, 22, 59)]
    container.clock = lambda: clock[0]

    async def slow_generation(messages, **kw):
        if kw.get("workflow") == "preview":
            clock[0] = datetime(2026, 12, 31, 23, 1)
        return await original(messages, **kw)

    fake_llm.chat = slow_generation
    async with transaction(container.factory) as s:
        p = await s.get(type(world.persona), world.persona.id)
        p.timezone = "Europe/Berlin"
    async with transaction(container.factory) as s:
        p = await s.get(type(world.persona), world.persona.id)
        await container.conversation(s).preview(p, "Сегодня какой день?")
    generator = "\n".join(m["content"] for m in fake_llm.gen_calls[0][0])
    guard = json.loads(fake_llm.guard_calls[0][0][1]["content"])
    assert "31 декабря 2026" in generator
    assert "1 января 2027" in guard["trusted_facts"]["persona_facts"]
