"""Pre-send guard: every AI-authored message is checked before it is queued; fail closed."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from sim.adapters.db.models import Dialog, Job, LlmCall, Message
from sim.adapters.db.session import transaction
from sim.clock import utcnow
from sim.domain.enums import Author, Delivery, Stage
from sim.ports.llm import LLMError, LLMResult
from sim.services import jobkinds
from sim.services.guard import (
    GUARD_HISTORY_TURNS,
    GuardError,
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


async def test_rewrite_then_ok_sends_the_fixed_reply(container, world, fake_llm):
    fake_llm.replies = [decision_json(reply=DRAFT)]
    fake_llm.guard_replies = [
        guard_json("rewrite", ["обещание дохода"], "Результат зависит от вас."),
        guard_json("ok"),
    ]
    await start(container, world)
    assert (await run_reply(container)).status == "sent"
    _, _, messages, _ = await state(container)
    (ai,) = ai_messages(messages)
    assert ai.text == "Результат зависит от вас." and ai.original_text == DRAFT
    assert ai.guard_verdict == "rewritten" and ai.guard_violations == ["обещание дохода"]
    assert ai.delivery is Delivery.QUEUED
    # the fixed reply is checked again: the second guard call sees it as the draft
    assert len(fake_llm.guard_calls) == 2
    second = json.loads(fake_llm.guard_calls[1][0][1]["content"])
    assert second["draft_reply"] == "Результат зависит от вас."


async def test_rewrite_then_failed_recheck_blocks(container, world, fake_llm):
    fake_llm.replies = [decision_json(reply=DRAFT)] * 2
    fake_llm.guard_replies = [
        guard_json("rewrite", ["обещание дохода"], "Всё равно гарантирую."),
        guard_json("rewrite", ["снова гарантия"], "Ещё раз."),
        guard_json("block", ["снова гарантия"]),
    ]
    await start(container, world)
    assert (await run_reply(container)).status == "needs_operator"
    await assert_blocked(container, ["снова гарантия"])


async def test_block_sends_nothing_and_calls_the_operator(container, world, fake_llm):
    fake_llm.replies = [decision_json(reply="Да, я живой человек.")] * 2
    fake_llm.guard_replies = [guard_json("block", ["выдаёт себя за человека"])] * 2
    await start(container, world)
    assert (await run_reply(container)).status == "needs_operator"
    await assert_blocked(container, ["выдаёт себя за человека"], text="Да, я живой человек.")


async def assert_blocked(container, violations, text=DRAFT):
    _, dialog, messages, jobs = await state(container)
    blocked, neutral = ai_messages(messages)
    assert dialog.needs_operator is True and dialog.ai_paused is True
    assert blocked.text == text and blocked.delivery is Delivery.CANCELLED
    assert neutral.text == "Я уточню и вернусь с ответом чуть позже."
    assert neutral.delivery is Delivery.QUEUED
    assert blocked.guard_verdict == "blocked" and blocked.guard_violations == violations
    assert "blocked by the guard" in blocked.error
    assert [j.kind for j in jobs if j.kind == jobkinds.SEND] == [jobkinds.SEND]
    assert dialog.last_message_text != text           # the draft never became the dialog preview


async def test_blocked_draft_is_not_part_of_the_next_prompt(container, world, fake_llm):
    fake_llm.replies = [decision_json(reply="Да, я живой человек.")] * 2
    fake_llm.guard_replies = [guard_json("block", ["ложь"])] * 2
    await start(container, world)
    await run_reply(container)
    await ingest(container, world.channel.id, inbound("Алло?", ext="m2"))
    _, dialog, _, _ = await state(container)
    async with transaction(container.factory) as s:
        (await s.get(Dialog, dialog.id)).ai_paused = False
    fake_llm.replies = [decision_json(reply="Здравствуйте!")]
    await run_reply(container)
    prompt = fake_llm.gen_calls[-1][0]
    assert all("живой человек" not in m["content"] for m in prompt)


async def test_guard_error_fails_closed(container, world, fake_llm):
    fake_llm.replies = [decision_json(reply="Привет!")]
    fake_llm.guard_replies = [LLMError("groq down")]
    await start(container, world)
    with pytest.raises(LLMError):
        await run_reply(container)
    _, _, messages, _ = await state(container)
    assert ai_messages(messages) == []                 # nothing queued, nothing stored


async def test_unparseable_verdict_fails_closed(container, world, fake_llm):
    fake_llm.replies = [decision_json(reply="Привет!")]
    fake_llm.guard_replies = ["да вроде нормально", ]
    await start(container, world)
    with pytest.raises(GuardError):
        await run_reply(container)
    _, _, messages, _ = await state(container)
    assert ai_messages(messages) == []


async def test_unknown_verdict_value_is_unusable(container, world, fake_llm):
    fake_llm.replies = [decision_json(reply="Привет!")]
    fake_llm.guard_replies = [json.dumps({"verdict": "maybe", "violations": []})]
    await start(container, world)
    with pytest.raises(GuardError):
        await run_reply(container)


async def make_due(container):
    async with transaction(container.factory) as s:
        for j in (await s.scalars(select(Job).where(Job.status == "pending"))).all():
            j.run_at = utcnow() - timedelta(seconds=1)


async def test_guard_error_is_retried_by_the_job_and_then_needs_operator(container, world, fake_llm):
    await ingest(container, world.channel.id, inbound("Привет"))
    async with transaction(container.factory) as s:
        (await s.scalars(select(Job).where(Job.kind == jobkinds.REPLY))).one().max_attempts = 2
    fake_llm.replies = [decision_json(reply="a"), decision_json(reply="b")]
    fake_llm.guard_replies = [LLMError("down"), LLMError("down")]
    worker = Worker(container)

    await make_due(container)
    await worker.tick()
    async with container.factory() as s:
        job = (await s.scalars(select(Job).where(Job.kind == jobkinds.REPLY))).one()
        assert job.status == "pending" and job.attempts == 1 and "down" in job.last_error
        assert (await s.scalars(select(Dialog))).unique().one().needs_operator is False
        assert (await s.scalars(select(Message).where(Message.author == Author.AI))).all() == []

    await make_due(container)
    await worker.tick()                                # last attempt fails: hand over to a human
    async with container.factory() as s:
        job = (await s.scalars(select(Job).where(Job.kind == jobkinds.REPLY))).one()
        assert job.status == "failed"
        assert (await s.scalars(select(Dialog))).unique().one().needs_operator is True
        assert (await s.scalars(select(Message).where(Message.author == Author.AI))).all() == []


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


@pytest.mark.parametrize("model", ["fake-model", "  FAKE-MODEL  "])
@pytest.mark.parametrize("rewrite", [False, True])
async def test_same_model_fallback_fails_closed(
    container, world, fake_llm, connector, model, rewrite
):
    fake_llm.replies = [decision_json(reply=DRAFT)]
    same = LLMResult(text=guard_json("ok"), model=model)
    prefix = [guard_json("rewrite", ["обещание"], "Как дела?")] if rewrite else []
    # A further retry could succeed, but must never be attempted.
    fake_llm.guard_replies = [*prefix, same, same, guard_json("ok")]
    await start(container, world)
    with pytest.raises(GuardError, match="fallback returned the generator's model"):
        await run_reply(container)
    caps = [kw["capability"] for _, kw in fake_llm.guard_calls]
    assert caps == (["structured"] if rewrite else []) + ["structured", "prefilter"]
    assert fake_llm.guard_replies == [guard_json("ok")]
    _, _, messages, jobs = await state(container)
    assert ai_messages(messages) == []
    assert not any(j.kind in (jobkinds.SEND, jobkinds.FOLLOWUP) for j in jobs)
    assert connector.port.sent == []
    if rewrite:
        for payload, _ in fake_llm.guard_calls[1:]:
            assert json.loads(payload[1]["content"])["draft_reply"] == "Как дела?"


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
    fake_llm.replies = [decision_json(reply=DRAFT)]
    fake_llm.guard_replies = [guard_json("rewrite", ["обещание"], "Как дела?"), guard_json("ok")]
    async with transaction(container.factory) as s:
        outcome = await container.followup(s).run(dialog.id, 0, last_in, container.conversation(s))
    assert outcome == "sent"
    assert len(fake_llm.guard_calls) == 1 + 2
    _, _, messages, _ = await state(container)
    follow = ai_messages(messages)[-1]
    assert follow.text == "Как дела?" and follow.guard_verdict == "rewritten"


async def test_blocked_followup_stops_the_ladder_and_needs_operator(container, world, fake_llm):
    fake_llm.replies = [decision_json(reply="Привет!")]
    await start(container, world)
    await run_reply(container)
    _, dialog, messages, _ = await state(container)
    last_in = max(m.id for m in messages if m.author is Author.CLIENT)
    fake_llm.replies = [decision_json(reply=DRAFT)] * 2
    fake_llm.guard_replies = [guard_json("block", ["обещание"])] * 2
    async with transaction(container.factory) as s:
        outcome = await container.followup(s).run(dialog.id, 0, last_in, container.conversation(s))
    assert outcome == "needs_operator"
    _, dialog, _, jobs = await state(container)
    assert dialog.needs_operator and dialog.followup_step == 0
    # no further rung was scheduled: only the job queued after the first reply exists
    assert len([j for j in jobs if j.kind == jobkinds.FOLLOWUP]) == 1


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
    # honesty floor: BLOCK a human claim on a direct question (fixed, not editable)
    assert "не должен утверждать, что персонаж человек" in system and "ЗАБЛОКИРОВАТЬ" in system
    # everything else is the persona's freedom
    for gone in ("медицинских", "политики", "выдуманных фактов", "мата", "без вопроса сам говорит, что он ИИ"):
        assert gone not in system
    assert "ВСЁ ОСТАЛЬНОЕ НЕ НАРУШЕНИЕ" in system and "широкая свобода" in system
    assert "Не исправляй черновик ради согласованности с прошлыми репликами" in system


def test_guard_prompt_is_the_owner_text_plus_the_fixed_honesty_rule():
    from sim.services.guard import GUARD_HONESTY

    assert guard_system_prompt("  мой промпт  ") == f"мой промпт\n\n{GUARD_HONESTY}"
    assert "не должен утверждать, что персонаж человек" in GUARD_HONESTY


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
    fake_llm.replies = [decision_json(reply=DRAFT)]
    fake_llm.guard_replies = [guard_json("rewrite", ["обещание"], "Всё зависит от вас."), guard_json("ok")]
    resp = await api.post(f"/api/personas/{world.persona.id}/preview", json={"message": "Привет"}, headers=h)
    body = resp.json()
    assert resp.status_code == 200
    assert body["reply"] == "Всё зависит от вас."
    assert body["guard"] == {
        "verdict": "rewritten", "violations": ["обещание"], "model": "fake-guard",
        "original_text": DRAFT,
    }
    assert body["decision"]["reply"] == DRAFT and body["model"] == "fake-model"


async def test_preview_blocked_returns_empty_reply_and_the_draft(api, world, fake_llm):
    h = {"X-CSRF-Token": await login(api, OWNER[0])}
    fake_llm.replies = [decision_json(reply="Я человек")] * 2
    fake_llm.guard_replies = [guard_json("block", ["ложь"])] * 2
    body = (await api.post(f"/api/personas/{world.persona.id}/preview",
                           json={"message": "Ты бот?"}, headers=h)).json()
    assert body["reply"] == "" and body["guard"]["verdict"] == "blocked"
    assert body["guard"]["original_text"] == "Я человек" and body["guard"]["violations"] == ["ложь"]


async def test_dialog_api_and_analytics_expose_the_guard(api, container, world, fake_llm):
    await login(api, OWNER[0])
    fake_llm.replies = [decision_json(reply=DRAFT)] * 2
    fake_llm.guard_replies = [guard_json("block", ["обещание дохода"])] * 2
    await start(container, world)
    await run_reply(container)
    _, dialog, _, _ = await state(container)
    body = (await api.get(f"/api/dialogs/{dialog.id}")).json()
    out = [m for m in body["messages"] if m["author"] == "ai"][0]
    assert out["delivery"] == "cancelled" and "blocked by the guard" in out["error"]
    assert out["guard"] == {"verdict": "blocked", "violations": ["обещание дохода"],
                            "model": "fake-guard", "original_text": None}
    assert [m["guard"] for m in body["messages"] if m["author"] == "client"] == [None]
    day = utcnow().date().isoformat()
    summary = (await api.get("/api/analytics/summary", params={"from": day, "to": day})).json()
    assert summary["guard"] == {"ok": 0, "rewritten": 0, "blocked": 1}
