"""`/reset` in a chat wipes the dialog history and the persona's context (testing from scratch)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from sim.adapters.db.models import Client, Dialog, Job, Message
from sim.adapters.db.session import transaction
from sim.domain.enums import ClientStatus, Stage
from sim.services import jobkinds
from sim.services.dialog_reset import is_reset
from tests.conftest import inbound
from tests.test_ingest import ingest


@pytest.mark.parametrize("text,expected", [
    ("/reset", True), ("  /RESET ", True), ("/reset@sim_bot", True),
    ("/reset please", False), ("reset", False), ("", False), (None, False),
])
def test_is_reset(text, expected):
    assert is_reset(text) is expected


async def seed_conversation(container, world) -> int:
    await ingest(container, world.channel.id, inbound("Привет", ext="1"))
    await ingest(container, world.channel.id, inbound("Расскажи о курсе", ext="2"))
    async with transaction(container.factory) as s:
        client = (await s.scalars(select(Client))).one()
        client.stage, client.needs, client.notes = Stage.INTERESTED, ["карьера"], "любит кофе"
        client.next_step, client.status = "дать ссылку", ClientStatus.NO_RESPONSE
        return (await s.scalars(select(Dialog))).one().id


async def test_reset_wipes_history_and_context_and_does_not_reply(container, world):
    dialog_id = await seed_conversation(container, world)
    res = await ingest(container, world.channel.id, inbound("/reset", ext="3"))
    assert res.reset and res.message is None and not res.reply_queued
    async with container.factory() as s:
        assert (await s.scalars(select(Message).where(Message.dialog_id == dialog_id))).all() == []
        client = (await s.scalars(select(Client))).one()
        assert (client.stage, client.status) == (Stage.FIRST_CONTACT, ClientStatus.ACTIVE)
        assert client.needs == [] and client.notes == "" and client.next_step == ""
        dialog = await s.get(Dialog, dialog_id)
        assert dialog.unread == 0 and dialog.last_message_text == ""
        pending = (await s.scalars(select(Job).where(
            Job.kind.in_((jobkinds.REPLY, jobkinds.FOLLOWUP)), Job.status == "pending"))).all()
        assert pending == []


async def test_the_next_message_starts_a_new_conversation(container, world):
    await seed_conversation(container, world)
    await ingest(container, world.channel.id, inbound("/reset", ext="3"))
    res = await ingest(container, world.channel.id, inbound("Привет ещё раз", ext="4"))
    assert res.reply_queued
    async with container.factory() as s:
        texts = [m.text for m in (await s.scalars(select(Message))).all()]
    assert texts == ["Привет ещё раз"]


async def test_reset_typed_on_the_connected_account_works_too(container, world):
    dialog_id = await seed_conversation(container, world)
    res = await ingest(container, world.channel.id, inbound("/reset", ext="5", from_me=True))
    assert res.reset
    async with container.factory() as s:
        assert (await s.scalars(select(Message).where(Message.dialog_id == dialog_id))).all() == []


async def test_reset_before_any_conversation_is_a_no_op(container, world):
    res = await ingest(container, world.channel.id, inbound("/reset", ext="1"))
    assert res.reset and res.dialog is None
    async with container.factory() as s:
        assert (await s.scalars(select(Message))).all() == []
