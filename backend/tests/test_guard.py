"""Guard: every AI-authored message is reviewed; it advises a rewrite, it never blocks the reply."""
from __future__ import annotations

import json
import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from sim.adapters.db.models import Dialog, Job, LlmCall, Message
from sim.adapters.db.session import transaction
from sim.clock import utcnow
from sim.domain.enums import Author, Delivery, Stage
from sim.ports.llm import LLMError, LLMResult
from sim.services import jobkinds
from sim.services.conversation import GUARD_UNAVAILABLE
from sim.services.guard import (
    GUARD_HISTORY_TURNS,
    GuardVerdict,
    approves,
    guard_system_prompt,
    guard_user_message,
    parse_verdict,
)
from sim.services.prompt import Turn
from sim.worker.loop import Worker
from tests.conftest import OWNER, decision_json, guard_json, inbound, login
from tests.test_conversation import run_reply, start, state
from tests.test_ingest import ingest

DRAFT = "Вы заработаете 100 000 в месяц, гарантирую!"


def ai_messages(messages):
    return [m for m in messages if m.author is Author.AI]


@pytest.mark.parametrize("reason", ["length", "max_tokens", " MAX_TOKENS "])
def test_truncated_guard_cannot_approve_even_complete_json(reason):
    result = LLMResult(text=guard_json("ok"), finish_reason=reason)
    assert parse_verdict(result) is None
    assert result.finish_reason == reason  # raw provider metadata remains available


async def test_ok_sends_the_draft_and_records_the_verdict(container, world, fake_llm):
    fake_llm.replies = [decision_json(reply="Привет!")]
    await start(container, world)
    outcome = await run_reply(container)
    assert outcome.status == "sent"
    _, _, messages, jobs = await state(container)
    (ai,) = ai_messages(messages)
    assert ai.text == "Привет!" and ai.delivery is Delivery.QUEUED
    assert ai.guard_verdict == "ok" and ai.guard_violations == []
    assert ai.guard_model == "fake-guard" and ai.original_text is None
    assert jobkinds.SEND in [j.kind for j in jobs]
    (messages_sent, kwargs), = fake_llm.guard_calls
    assert kwargs["capability"] == "structured" and kwargs["json_mode"] is True
    assert kwargs["model"] is None and kwargs["workflow"] == "guard"
    assert [m["role"] for m in messages_sent] == ["system", "user"]
    async with container.factory() as s:
        calls = (await s.scalars(select(LlmCall).where(LlmCall.workflow == "guard"))).all()
    assert len(calls) == 1 and calls[0].capability == "structured" and calls[0].model == "fake-guard"


async def test_remarks_become_recommendations_and_the_rewrite_goes_out(container, world, fake_llm):
    corrected = "Доход зависит от многих факторов."
    fake_llm.replies = [decision_json(reply=DRAFT), decision_json(reply=corrected)]
    fake_llm.guard_replies = [guard_json("rewrite", ["обещание дохода"], "Текст контролёра не отправлять.")]
    await start(container, world)
    assert (await run_reply(container)).status == "sent"
    _, dialog, messages, _ = await state(container)
    (ai,) = ai_messages(messages)
    assert ai.text == corrected.rstrip(".") and ai.delivery is Delivery.QUEUED
    # The remarks stay recorded on the sent rewrite; it is not reviewed a second time.
    assert ai.guard_verdict == "ok" and ai.guard_violations == ["обещание дохода"]
    assert len(fake_llm.gen_calls) == 2 and len(fake_llm.guard_calls) == 1
    retry = "\n".join(message["content"] for message in fake_llm.gen_calls[1][0])
    assert "тип: review" in retry and "обещание дохода" in retry
    assert DRAFT not in retry and "Текст контролёра" not in retry
    assert not dialog.needs_operator and not dialog.ai_paused


@pytest.mark.parametrize("violations,fixed", [(["ложное обещание"], ""), ([], "Guard replacement")])
async def test_ok_with_remarks_still_asks_for_one_rewrite(container, world, fake_llm, violations, fixed):
    fake_llm.replies = [decision_json(reply=DRAFT),
                        decision_json(reply="Результат не гарантирован.")]
    fake_llm.guard_replies = [guard_json("ok", violations, fixed)]
    await start(container, world)
    assert (await run_reply(container)).status == "sent"
    retry = "\n".join(message["content"] for message in fake_llm.gen_calls[1][0])
    assert "тип: review" in retry and DRAFT not in retry and "Guard replacement" not in retry
    assert all(violation in retry for violation in violations)
    _, _, messages, _ = await state(container)
    assert ai_messages(messages)[0].text == "Результат не гарантирован"


async def test_a_second_objection_never_reaches_the_operator(container, world, fake_llm):
    fake_llm.replies = [decision_json(reply="Привет! Ого, в такую рань?")] * 2
    fake_llm.guard_replies = [guard_json("block", ["время клиента не подтверждено"])]
    await start(container, world)
    assert (await run_reply(container)).status == "sent"
    _, dialog, messages, jobs = await state(container)
    (ai,) = ai_messages(messages)
    assert ai.text == "Привет! Ого, в такую рань?" and ai.delivery is Delivery.QUEUED
    assert not dialog.needs_operator and not dialog.ai_paused
    assert all(m.text != "Я уточню и вернусь с ответом чуть позже." for m in messages)
    assert sum(j.kind == jobkinds.SEND for j in jobs) == 1


async def test_rejected_first_draft_is_not_part_of_the_next_prompt(container, world, fake_llm):
    fake_llm.replies = [decision_json(reply="Дам вам 100% гарантию дохода!"),
                        decision_json(reply="Доход не гарантирован.")]
    fake_llm.guard_replies = [guard_json("block", ["обещание дохода"])]
    await start(container, world)
    await run_reply(container)
    await ingest(container, world.channel.id, inbound("Алло?", ext="m2"))
    fake_llm.replies = [decision_json(reply="Здравствуйте!")]
    await run_reply(container)
    prompt = fake_llm.gen_calls[-1][0]
    assert all("100% гарантию" not in m["content"] for m in prompt)


@pytest.mark.parametrize("failure", [
    LLMError("groq down"), "да вроде нормально", json.dumps({"verdict": "maybe", "violations": []}),
])
async def test_guard_failure_does_not_hold_the_reply(container, world, fake_llm, failure):
    fake_llm.replies = [decision_json(reply="Привет!")]
    fake_llm.guard_replies = [failure]
    await start(container, world)
    assert (await run_reply(container)).status == "sent"
    _, dialog, messages, _ = await state(container)
    (ai,) = ai_messages(messages)
    assert ai.text == "Привет!" and ai.guard_verdict == "ok"
    assert ai.guard_violations == [GUARD_UNAVAILABLE] and ai.guard_model is None
    assert not dialog.needs_operator and len(fake_llm.gen_calls) == 1


async def make_due(container):
    async with transaction(container.factory) as s:
        for j in (await s.scalars(select(Job).where(Job.status == "pending"))).all():
            j.run_at = utcnow() - timedelta(seconds=1)


async def test_same_model_as_generator_triggers_the_fallback_capability(container, world, fake_llm):
    fake_llm.replies = [decision_json(reply="Привет!")]
    same = LLMResult(text=guard_json("ok"), model="fake-model")         # the generator's own model
    fake_llm.guard_replies = [same, guard_json("ok")]                    # fallback: another model
    await start(container, world)
    assert (await run_reply(container)).status == "sent"
    caps = [kw["capability"] for _, kw in fake_llm.guard_calls]
    assert caps == ["structured", "prefilter"]
    _, _, messages, _ = await state(container)
    assert ai_messages(messages)[0].guard_model == "fake-guard"
    async with container.factory() as s:
        rows = (await s.scalars(select(LlmCall).where(LlmCall.workflow == "guard"))).all()
    assert sorted(r.capability for r in rows) == ["prefilter", "structured"]
    assert {r.model for r in rows} == {"fake-model", "fake-guard"}   # both models are on record


@pytest.mark.parametrize("verdict,violations,fixed", [
    ("block", ["Available synthetic media was declined without a reason"], ""),
    ("rewrite", ["Unjustified handoff"], "Replacement must not be sent"),
    ("ok", ["Unjustified handoff"], ""),
    ("ok", [], "Replacement must not be sent"),
])
async def test_same_model_objection_is_kept_and_the_decision_rewritten(
    container, world, fake_llm, connector, verdict, violations, fixed,
):
    # An objection is never erased by asking another rotation; she rewrites with it.
    rejected = "No real video exists. Passing your request to a person."
    fake_llm.replies = [
        decision_json(reply=rejected, handoff_to_operator=True,
                      handoff_reason="Unjustified identity verification",
                      media={"kind": "video_note", "decision": "decline",
                             "reason": "No real video exists"}),
        decision_json(reply="A synthetic clip is possible."),
    ]
    fake_llm.guard_replies = [LLMResult(text=guard_json(verdict, violations, fixed), model="fake-model")]
    await start(container, world, "Please send a video note")
    assert (await run_reply(container)).status == "sent"
    _, dialog, messages, jobs = await state(container)
    assert not dialog.ai_paused and not dialog.needs_operator
    queued = [m for m in ai_messages(messages) if m.delivery is Delivery.QUEUED]
    assert [m.text for m in queued] == ["A synthetic clip is possible"]
    assert all(m.text != rejected for m in messages)
    assert sum(j.kind == jobkinds.SEND for j in jobs) == 1
    assert connector.port.sent == []
    assert [kw["capability"] for _, kw in fake_llm.guard_calls] == ["structured"]
    retry = "\n".join(message["content"] for message in fake_llm.gen_calls[1][0])
    assert rejected not in retry and "тип: review" in retry and "Replacement" not in retry
    assert all(violation in retry for violation in violations)


async def test_independent_block_after_same_model_ok_is_preserved(container, world, fake_llm):
    fake_llm.replies = [decision_json(reply="Rejected draft"), decision_json(reply="Repaired reply")]
    fake_llm.guard_replies = [
        LLMResult(text=guard_json("ok"), model="fake-model"),
        guard_json("block", ["Unsupported claim"]),
    ]
    await start(container, world)
    assert (await run_reply(container)).status == "sent"
    _, dialog, messages, _ = await state(container)
    assert not dialog.ai_paused and not dialog.needs_operator
    assert [m.text for m in ai_messages(messages)] == ["Repaired reply"]
    retry = "\n".join(message["content"] for message in fake_llm.gen_calls[1][0])
    assert "Unsupported claim" in retry and "тип: review" in retry


@pytest.mark.parametrize("model", ["fake-model", "  FAKE-MODEL  "])
async def test_same_model_fallback_counts_as_a_guard_outage(container, world, fake_llm, connector, model):
    fake_llm.replies = [decision_json(reply="Без обещаний.")]
    same = LLMResult(text=guard_json("ok"), model=model)
    fake_llm.guard_replies = [same, same, guard_json("ok")]
    await start(container, world)
    assert (await run_reply(container)).status == "sent"
    assert [kw["capability"] for _, kw in fake_llm.guard_calls] == ["structured", "prefilter"]
    _, _, messages, _ = await state(container)
    (ai,) = ai_messages(messages)
    assert ai.text == "Без обещаний" and ai.guard_violations == [GUARD_UNAVAILABLE]


@pytest.mark.parametrize("missing", ["generator", "primary", "fallback"])
async def test_missing_model_metadata_preserves_existing_behavior(
    container, world, fake_llm, missing
):
    # Missing identity does not prove independence, but is not a known collision.
    fake_llm.replies = [LLMResult(
        text=decision_json(reply="Привет!"),
        model=None if missing == "generator" else "fake-model",
    )]
    fake_llm.guard_replies = [LLMResult(
        text=guard_json("ok"), model=None if missing == "primary" else "fake-model",
    )]
    if missing == "fallback":
        fake_llm.guard_replies.append(LLMResult(text=guard_json("ok"), model=None))
    await start(container, world)
    assert (await run_reply(container)).status == "sent"
    caps = [kw["capability"] for _, kw in fake_llm.guard_calls]
    assert caps == (["structured", "prefilter"] if missing == "fallback" else ["structured"])


async def test_followups_are_guarded_too(container, world, fake_llm):
    fake_llm.replies = [decision_json(reply="Привет!")]
    await start(container, world)
    await run_reply(container)
    _, dialog, messages, _ = await state(container)
    last_in = max(m.id for m in messages if m.author is Author.CLIENT)
    fake_llm.replies = [decision_json(reply=DRAFT), decision_json(reply="Результат зависит от вас.")]
    fake_llm.guard_replies = [guard_json("block", ["обещание"])]
    async with transaction(container.factory) as s:
        outcome = await container.followup(s).run(dialog.id, 0, last_in, container.conversation(s))
    assert outcome == "sent"
    assert len(fake_llm.guard_calls) == 1 + 1
    _, dialog, messages, _ = await state(container)
    follow = ai_messages(messages)[-1]
    assert follow.text == "Результат зависит от вас" and follow.guard_violations == ["обещание"]
    assert not dialog.needs_operator and dialog.followup_step == 1


async def test_operator_messages_are_not_guarded(container, world, fake_llm, connector):
    await ingest(container, world.channel.id, inbound("Привет"))
    async with transaction(container.factory) as s:
        dialog = (await s.scalars(select(Dialog))).unique().one()
        msg = await container.outbound(s).send_manual(dialog, 1, "Оператор на связи")
        msg_id = msg.id
    await make_due(container)
    await Worker(container).tick()
    assert connector.port.sent == [("chat-1", "Оператор на связи")]
    assert fake_llm.calls == []
    async with container.factory() as s:
        m = await s.get(Message, msg_id)
        assert m.guard_verdict is None and m.guard_model is None


# ---- prompts ------------------------------------------------------------------------------
def test_guard_checks_only_the_critical_rules():
    system = guard_system_prompt()
    for rule in ("пароли, коды из SMS и данные карт", "Никаких ссылок, кроме", "гарантированного дохода",
                 "просьбу не писать"):
        assert rule in system
    assert "Нельзя выдавать себя за реального человека" in system
    # Ordinary style remains free; match profanity as a word, not inside "формата".
    for gone in ("медицинских", "политики", "выдуманных фактов", "без вопроса сам говорит, что он ИИ"):
        assert gone not in system
    assert re.search(r"\bмата\b", system) is None
    assert "ВСЁ ОСТАЛЬНОЕ НЕ НАРУШЕНИЕ" in system and "широкая свобода" in system
    assert "Не исправляй черновик ради согласованности с прошлыми репликами" in system


def test_guard_prompt_is_the_owner_text_plus_decision_control():
    from sim.services.guard import identity_control
    from sim.services.instructions import FACT_CONTROL, GUARD_CONTROL, MEDIA_CONTROL, VOICE_CONTROL

    assert guard_system_prompt("  мой промпт  ") == f"мой промпт\n\n{GUARD_CONTROL}\n\n{FACT_CONTROL}\n\n{VOICE_CONTROL}\n\n{MEDIA_CONTROL}\n\n{identity_control()}"


def test_guard_sees_the_same_history_window_as_the_persona():
    from sim.services.prompt import HISTORY_LIMIT

    assert GUARD_HISTORY_TURNS == HISTORY_LIMIT


def test_guard_rewrite_gets_plain_messenger_punctuation():
    raw = guard_json("rewrite", ["v"], fixed="Ну смотри — это «просто»; правда…")
    verdict = parse_verdict(LLMResult(text=raw, model="m", latency_ms=1))
    assert verdict is not None and verdict.fixed_reply == 'Ну смотри - это "просто", правда...'


def test_guard_system_prompt_is_byte_stable():
    a, b = guard_system_prompt(), guard_system_prompt()
    assert a == b
    for noise in ("2026", "ref=", "{ref}", "Анна", "Марина"):
        assert noise not in a


def test_guard_user_message_has_last_turns_draft_stage_and_links_only_here():
    total = GUARD_HISTORY_TURNS + 4
    history = [Turn("user" if i % 2 == 0 else "assistant", f"turn {i}") for i in range(total)]
    payload = json.loads(
        guard_user_message(history, "черновик", Stage.ENGAGED, ["https://rcipm.com/?ref=abc"])
    )
    assert [t["text"] for t in payload["dialog"]] == [f"turn {i}" for i in range(4, total)]
    assert len(payload["dialog"]) == GUARD_HISTORY_TURNS
    assert {t["role"] for t in payload["dialog"]} == {"client", "persona"}
    assert payload["draft_reply"] == "черновик" and payload["stage"] == "engaged"
    assert payload["allowed_links"] == ["https://rcipm.com/?ref=abc"]


async def test_guard_prompt_sent_to_the_broker_is_identical_for_two_clients(container, world, fake_llm):
    await ingest(container, world.channel.id, inbound("Привет", user="u1", thread="c1", ext="a"))
    await ingest(container, world.channel.id, inbound("Hello", user="u2", thread="c2", ext="b"))
    async with transaction(container.factory) as s:
        for dialog in (await s.scalars(select(Dialog))).unique().all():
            await container.conversation(s).run_reply(dialog.id)
    first, second = (c[0] for c in fake_llm.guard_calls)
    assert first[0] == second[0]                       # system: byte-identical
    assert first[1] != second[1]                       # user: variable data
    assert first[0]["content"] == guard_system_prompt()


# ---- preview and analytics ----------------------------------------------------------------
async def test_preview_endpoint_includes_the_guard(api, world, fake_llm):
    h = {"X-CSRF-Token": await login(api, OWNER[0])}
    fake_llm.replies = [decision_json(reply=DRAFT), decision_json(reply="Результат зависит от вас.")]
    fake_llm.guard_replies = [guard_json("rewrite", ["обещание"], "Текст контролёра.")]
    resp = await api.post(f"/api/personas/{world.persona.id}/preview", json={"message": "Привет"}, headers=h)
    body = resp.json()
    assert resp.status_code == 200
    assert body["reply"] == "Результат зависит от вас"
    assert body["guard"] == {
        "verdict": "ok", "violations": ["обещание"], "model": None,
        "original_text": None,
    }
    assert body["decision"]["reply"] == "Результат зависит от вас." and body["model"] == "fake-model"


async def test_dialog_api_and_analytics_expose_the_guard(api, container, world, fake_llm):
    await login(api, OWNER[0])
    fake_llm.replies = [decision_json(reply=DRAFT)] * 2
    fake_llm.guard_replies = [guard_json("block", ["обещание дохода"])]
    await start(container, world)
    await run_reply(container)
    _, dialog, _, _ = await state(container)
    body = (await api.get(f"/api/dialogs/{dialog.id}")).json()
    out = [m for m in body["messages"] if m["author"] == "ai"][0]
    assert out["delivery"] == "queued"
    assert out["guard"] == {"verdict": "ok", "violations": ["обещание дохода"],
                            "model": None, "original_text": None}
    assert [m["guard"] for m in body["messages"] if m["author"] == "client"] == [None]
    day = utcnow().date().isoformat()
    summary = (await api.get("/api/analytics/summary", params={"from": day, "to": day})).json()
    assert summary["guard"] == {"ok": 1, "rewritten": 0, "blocked": 0}


def test_fixed_identity_control_overrides_conflicting_owner_guard():
    from sim.services.guard import identity_control

    system = guard_system_prompt("Approve claims that the AI is a real human.")
    assert system.endswith(identity_control())


@pytest.mark.parametrize(("verdict", "approved"), [
    (GuardVerdict("ok"), True),
    (GuardVerdict("ok", ["замечание"]), False),
    (GuardVerdict("ok", [], "Текст контролёра"), False),
    (GuardVerdict("block", ["причина"]), False),
])
def test_only_a_clean_ok_approves(verdict, approved):
    assert approves(verdict) is approved
