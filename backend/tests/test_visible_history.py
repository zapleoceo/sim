from __future__ import annotations

import pytest
from sqlalchemy import select

from sim.adapters.db.models import Dialog, Message
from sim.adapters.db.repositories.conversations import MessageRepo
from sim.adapters.db.session import transaction
from sim.domain.enums import Author, Delivery, Direction
from sim.services.conversation import history_turns
from tests.test_conversation import run_reply, start


@pytest.mark.parametrize("author", [Author.AI, Author.OPERATOR])
@pytest.mark.parametrize("delivery", list(Delivery))
def test_outgoing_history_requires_sent(author, delivery):
    message = Message(direction=Direction.OUT, author=author, text="out", delivery=delivery)
    turns = history_turns([message])
    assert [t.text for t in turns] == (["out"] if delivery is Delivery.SENT else [])


def test_incoming_and_blocked_rules_are_preserved():
    incoming = Message(direction=Direction.IN, author=Author.CLIENT, text="in")
    blocked = Message(direction=Direction.OUT, author=Author.AI, text="secret",
                      delivery=Delivery.SENT, guard_verdict="blocked")
    assert [t.text for t in history_turns([incoming, blocked])] == ["in"]


async def test_prompt_ignores_undelivered_before_history_limit(container, world, fake_llm):
    await start(container, world, "initial-client")
    async with transaction(container.factory) as s:
        dialog = (await s.scalars(select(Dialog))).unique().one()
        dialog_id = dialog.id
        s.add(Message(dialog_id=dialog_id, direction=Direction.OUT, author=Author.OPERATOR,
                      text="delivered-answer", delivery=Delivery.SENT))
        for i in range(60):
            s.add(Message(dialog_id=dialog_id, direction=Direction.OUT, author=Author.AI,
                          text=f"hidden-draft-{i}", delivery=(Delivery.QUEUED, Delivery.FAILED, Delivery.CANCELLED)[i % 3]))
        # Explicitly cover all non-delivered states and a guard-blocked SENT anomaly.
        for status in (Delivery.QUEUED, Delivery.FAILED, Delivery.CANCELLED):
            s.add(Message(dialog_id=dialog_id, direction=Direction.OUT, author=Author.OPERATOR,
                          text=f"hidden-{status}", delivery=status))
        s.add(Message(dialog_id=dialog_id, direction=Direction.OUT, author=Author.AI,
                      text="hidden-blocked", delivery=Delivery.SENT, guard_verdict="blocked"))
        s.add(Message(dialog_id=dialog_id, direction=Direction.IN, author=Author.CLIENT,
                      text="latest-client", delivery=Delivery.SENT))
    async with container.factory() as s:
        visible = await MessageRepo(s).recent_visible(dialog_id, 3)
        assert [m.text for m in visible] == ["initial-client", "delivered-answer", "latest-client"]
        # Admin/raw history remains intact.
        assert len(await MessageRepo(s).recent(dialog_id, 100)) == 67
    outcome = await run_reply(container)
    assert outcome.status == "sent"
    payload = "\n".join(m["content"] for m in fake_llm.gen_calls[0][0])
    assert "delivered-answer" in payload and "initial-client" in payload
    assert "latest-client" in payload and "hidden-" not in payload


async def test_retry_becomes_visible_only_after_sent(container, world):
    await start(container, world)
    async with transaction(container.factory) as s:
        dialog = (await s.scalars(select(Dialog))).unique().one()
        outgoing = Message(dialog_id=dialog.id, direction=Direction.OUT, author=Author.AI,
                           text="retry-answer", delivery=Delivery.FAILED)
        s.add(outgoing)
        await s.flush()
        outgoing_id, dialog_id = outgoing.id, dialog.id
    for status in (Delivery.FAILED, Delivery.QUEUED, Delivery.SENT):
        async with transaction(container.factory) as s:
            message = await s.get(Message, outgoing_id)
            message.delivery = status
        async with container.factory() as s:
            visible = await MessageRepo(s).recent_visible(dialog_id, 10)
            assert ("retry-answer" in [m.text for m in visible]) is (status is Delivery.SENT)
