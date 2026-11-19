"""Controller feedback, state gating and neutral handoff (CI-only verification)."""
from __future__ import annotations

import json

from sim.domain.enums import ClientStatus, Delivery, Stage
from sim.services import jobkinds
from tests.conftest import decision_json, guard_json
from tests.test_conversation import run_reply, start, state


async def test_guard_rejection_retries_with_clean_context_then_applies_only_new_decision(
    container, world, fake_llm
):
    fake_llm.replies = [
        decision_json(reply="Плохой ответ", stage="engaged", needs=["неверно"]),
        decision_json(reply="Уточню детали", stage="needs_identified", needs=["новая цель"]),
    ]
    fake_llm.guard_replies = [guard_json("block", ["этап не следует из диалога"]), guard_json("ok")]
    await start(container, world)
    result = await run_reply(container)
    client, dialog, messages, _ = await state(container)
    assert result.status == "sent"
    assert client.stage is Stage.NEEDS_IDENTIFIED and client.needs == ["новая цель"]
    assert not dialog.needs_operator
    assert messages[-1].text == "Уточню детали"
    retry = "\n".join(message["content"] for message in fake_llm.gen_calls[1][0])
    assert "тип: review" in retry
    assert "этап не следует из диалога" not in retry and "Плохой ответ" not in retry
    first_guard = json.loads(fake_llm.guard_calls[0][0][1]["content"])
    assert first_guard["decision"]["stage"] == "engaged"
    assert first_guard["decision"]["needs"] == ["неверно"]
    assert first_guard["decision"]["handoff_to_operator"] is False


async def test_two_guard_rejections_pause_once_without_mutating_client(
    container, world, fake_llm
):
    fake_llm.replies = [
        decision_json(reply="Плохой ответ 1", stage="engaged", needs=["ошибка"]),
        decision_json(reply="Плохой ответ 2", stage="needs_identified", stop_contact=True),
    ]
    fake_llm.guard_replies = [
        guard_json("block", ["неверное решение"]),
        guard_json("block", ["вторая ошибка"]),
    ]
    await start(container, world)
    first = await run_reply(container)
    second = await run_reply(container)
    client, dialog, messages, jobs = await state(container)
    assert first.status == "needs_operator" and second.status == "skipped"
    assert client.stage is Stage.FIRST_CONTACT and client.needs == []
    assert client.status is ClientStatus.ACTIVE and not client.no_followups
    assert dialog.ai_paused and dialog.needs_operator
    assert len([m for m in messages if m.delivery is Delivery.CANCELLED]) == 1
    assert len([m for m in messages if m.text == "Я уточню и вернусь с ответом чуть позже."]) == 1
    assert [j.kind for j in jobs].count(jobkinds.SEND) == 1
    assert not any(j.kind in {jobkinds.VIDEO_NOTE, jobkinds.PERSONA_MEDIA} for j in jobs)


async def test_preview_carries_forward_only_approved_context(container, world, fake_llm):
    conversation = None
    async with container.factory() as session:
        conversation = container.conversation(session)
        fake_llm.replies = [decision_json(reply="Первый", stage="engaged", needs=["цель"])]
        first = await conversation.preview(world.persona, "Привет")
        assert first.context == {
            "stage": "engaged", "status": "active", "needs": ["цель"], "next_step": "узнать цели",
        }
        fake_llm.replies = [decision_json(reply="Ошибка", stage="needs_identified")] * 2
        fake_llm.guard_replies = [guard_json("block", ["ошибка"])] * 2
        second = await conversation.preview(
            world.persona, "Продолжим", stage=Stage(first.context["stage"]),
            needs=first.context["needs"], next_step=first.context["next_step"],
        )
        assert second.reply == ""
        assert second.context == first.context
