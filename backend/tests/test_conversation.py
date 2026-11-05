from __future__ import annotations

import pytest
from sqlalchemy import select

from sim.adapters.db.models import Client, Dialog, Job, LlmCall, Message
from sim.adapters.db.session import transaction
from sim.domain.enums import Author, ClientStatus, Delivery, Stage
from sim.ports.llm import LLMError
from sim.services import jobkinds
from tests.conftest import decision_json, inbound, make_world
from tests.test_ingest import ingest


async def run_reply(container, **kw):
    async with transaction(container.factory) as s:
        dialog = (await s.scalars(select(Dialog))).unique().one()
        return await container.conversation(s).run_reply(dialog.id, **kw)


async def state(container):
    async with container.factory() as s:
        return (
            (await s.scalars(select(Client))).one(),
            (await s.scalars(select(Dialog))).unique().one(),
            list((await s.scalars(select(Message).order_by(Message.id))).all()),
            list((await s.scalars(select(Job).order_by(Job.id))).all()),
        )


async def start(container, world, text="Здравствуйте"):
    await ingest(container, world.channel.id, inbound(text))


async def test_reply_applies_decision_and_queues_send_and_followup(container, world, fake_llm):
    fake_llm.replies = [
        decision_json(stage="needs_identified", needs=["карьера"], next_step="ссылка")
    ]
    await start(container, world)
    outcome = await run_reply(container)
    assert outcome.status == "sent"
    client, dialog, messages, jobs = await state(container)
    assert client.stage is Stage.NEEDS_IDENTIFIED and client.needs == ["карьера"]
    assert client.next_step == "ссылка"
    out = messages[-1]
    assert out.author is Author.AI and out.delivery is Delivery.QUEUED and out.model == "fake-model"
    kinds = [j.kind for j in jobs]
    assert jobkinds.SEND in kinds and jobkinds.FOLLOWUP in kinds
    async with container.factory() as s:
        call = (await s.scalars(select(LlmCall))).one()
        assert call.cached_tokens == 50 and call.workflow == "reply" and call.dialog_id == dialog.id


async def test_prompt_sent_to_broker_has_expected_layout(container, world, fake_llm):
    await start(container, world, "Меня зовут Анна")
    await run_reply(container)
    messages, kwargs = fake_llm.calls[0]
    assert [m["role"] for m in messages] == ["system", "user", "system"]
    assert kwargs["json_mode"] is True and kwargs["capability"] == "chat:sales"
    assert kwargs["model"] is None
    assert "Меня зовут Анна" in messages[1]["content"]
    client, *_ = await state(container)
    assert f"ref={client.ref_code}" in messages[-1]["content"]


async def test_persona_model_is_pinned(container, fake_llm):
    w = await make_world(container, persona_name="Pinned", model="claude-sub/claude-sonnet-5-5")
    await start(container, w)
    await run_reply(container)
    assert fake_llm.calls[0][1]["model"] == "claude-sub/claude-sonnet-5-5"


async def test_invalid_json_is_retried_once(container, world, fake_llm):
    fake_llm.replies = ["not json at all", decision_json(reply="Ок")]
    await start(container, world)
    outcome = await run_reply(container)
    assert outcome.status == "sent" and len(fake_llm.calls) == 2
    assert "not a valid JSON" in fake_llm.calls[1][0][-1]["content"]


async def test_invalid_json_twice_marks_needs_operator_and_sends_nothing(
    container, world, fake_llm
):
    fake_llm.replies = ["nope", "still nope"]
    await start(container, world)
    outcome = await run_reply(container)
    assert outcome.status == "needs_operator"
    _, dialog, messages, jobs = await state(container)
    assert dialog.needs_operator and len(messages) == 1
    assert [j.kind for j in jobs] == [jobkinds.REPLY]


async def test_ai_cannot_jump_to_registered(container, world, fake_llm):
    fake_llm.replies = [decision_json(stage="registered")]
    await start(container, world)
    await run_reply(container)
    client, *_ = await state(container)
    assert client.stage is Stage.FIRST_CONTACT


@pytest.mark.parametrize(
    ("flags", "no_followups"),
    [({"refused": True}, False), ({"stop_contact": True}, True)],
)
async def test_refusal_and_stop_contact(container, world, fake_llm, flags, no_followups):
    fake_llm.replies = [decision_json(reply="Хорошо, спасибо!", **flags)]
    await start(container, world)
    await run_reply(container)
    client, _, _, jobs = await state(container)
    assert client.status is ClientStatus.REFUSED and client.no_followups is no_followups
    assert not [j for j in jobs if j.kind == jobkinds.FOLLOWUP and j.status == "pending"]


async def test_empty_reply_sends_nothing(container, world, fake_llm):
    fake_llm.replies = [decision_json(reply="", stage="engaged")]
    await start(container, world)
    outcome = await run_reply(container)
    assert outcome.status == "empty"
    _, _, messages, _ = await state(container)
    assert len(messages) == 1


async def test_handoff_pauses_ai(container, world, fake_llm):
    fake_llm.replies = [decision_json(handoff_to_operator=True)]
    await start(container, world)
    await run_reply(container)
    _, dialog, messages, _ = await state(container)
    assert dialog.needs_operator and dialog.ai_paused and len(messages) == 2


async def test_paused_dialog_is_skipped(container, world, fake_llm):
    await start(container, world)
    async with transaction(container.factory) as s:
        (await s.scalars(select(Dialog))).unique().one().ai_paused = True
    assert (await run_reply(container)).status == "skipped"
    assert fake_llm.calls == []


async def test_broker_error_propagates_and_is_audited(container, world, fake_llm):
    fake_llm.replies = [LLMError("down")]
    await start(container, world)
    with pytest.raises(LLMError):
        await run_reply(container)
    async with container.factory() as s:
        assert (await s.scalars(select(LlmCall))).one().error == "down"


async def test_preview_persists_nothing(container, world, fake_llm):
    async with transaction(container.factory) as s:
        res = await container.conversation(s).preview(world.persona, "Привет!")
    assert res.reply and res.decision is not None
    async with container.factory() as s:
        assert (await s.scalars(select(Message))).all() == []
        assert (await s.scalars(select(Client))).all() == []
    assert "PREVIEW" not in fake_llm.calls[0][0][0]["content"]
