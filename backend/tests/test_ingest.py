from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from sim.adapters.db.models import Channel, Client, Dialog, FunnelEvent, Job, Message
from sim.adapters.db.session import transaction
from sim.domain.enums import ClientStatus, Delivery, Direction
from sim.services import jobkinds
from tests.conftest import inbound


async def ingest(container, channel_id, msg):
    async with transaction(container.factory) as s:
        channel = await s.get(Channel, channel_id)
        return await container.ingest(s).ingest(channel, msg)


async def test_creates_client_dialog_message_and_reply_job(container, world):
    res = await ingest(container, world.channel.id, inbound("Привет", name="Анна"))
    assert not res.duplicate and res.reply_queued
    async with container.factory() as s:
        client = (await s.scalars(select(Client))).one()
        assert client.display_name == "Анна" and len(client.ref_code) == 8
        dialog = (await s.scalars(select(Dialog))).unique().one()
        assert dialog.unread == 1 and dialog.last_message_text == "Привет"
        msg = (await s.scalars(select(Message))).one()
        assert msg.direction is Direction.IN and msg.delivery is Delivery.SENT
        job = (await s.scalars(select(Job))).one()
        assert job.kind == jobkinds.REPLY and job.payload == {"dialog_id": dialog.id}


async def test_dedup_by_external_id(container, world):
    await ingest(container, world.channel.id, inbound("Привет", ext="same"))
    again = await ingest(container, world.channel.id, inbound("Привет", ext="same"))
    assert again.duplicate and again.message is None
    async with container.factory() as s:
        assert len((await s.scalars(select(Message))).all()) == 1
        assert len((await s.scalars(select(Job))).all()) == 1


async def test_burst_debounces_into_one_reply_job(container, world):
    await ingest(container, world.channel.id, inbound("a", ext="1"))
    async with container.factory() as s:
        first_run = (await s.scalars(select(Job))).one().run_at
    await ingest(container, world.channel.id, inbound("b", ext="2"))
    async with container.factory() as s:
        jobs = (await s.scalars(select(Job))).all()
        assert len(jobs) == 1 and jobs[0].run_at >= first_run


async def test_returning_client_goes_back_to_active_with_event(container, world):
    await ingest(container, world.channel.id, inbound("a", ext="1"))
    async with transaction(container.factory) as s:
        (await s.scalars(select(Client))).one().status = ClientStatus.NO_RESPONSE
    res = await ingest(container, world.channel.id, inbound("я вернулась", ext="2"))
    assert res.status_before is ClientStatus.NO_RESPONSE
    async with container.factory() as s:
        assert (await s.scalars(select(Client))).one().status is ClientStatus.ACTIVE
        event = (await s.scalars(select(FunnelEvent))).one()
        assert (event.from_value, event.to_value) == ("no_response", "active")


async def test_refused_client_stays_refused_and_gets_no_reply_job(container, world):
    await ingest(container, world.channel.id, inbound("a", ext="1"))
    async with transaction(container.factory) as s:
        (await s.scalars(select(Client))).one().status = ClientStatus.REFUSED
        for job in (await s.scalars(select(Job))).all():
            await s.delete(job)
    res = await ingest(container, world.channel.id, inbound("стоп", ext="2"))
    assert not res.reply_queued
    async with container.factory() as s:
        assert (await s.scalars(select(Client))).one().status is ClientStatus.REFUSED
        assert (await s.scalars(select(Job))).all() == []


async def test_own_message_pauses_ai_and_echo_is_ignored(container, world):
    await ingest(container, world.channel.id, inbound("a", ext="1"))
    async with transaction(container.factory) as s:
        dialog = (await s.scalars(select(Dialog))).unique().one()
        await container.outbound(s).queue_ai(dialog, "Здравствуйте!", None)
    echo = inbound("Здравствуйте!", ext="echo-1", from_me=True)
    res = await ingest(container, world.channel.id, echo)
    assert res.duplicate
    async with container.factory() as s:
        assert (await s.scalars(select(Dialog))).unique().one().ai_paused is False

    human = inbound(
        "Это живой оператор с телефона",
        ext="h1",
        from_me=True,
        at=echo.occurred_at + timedelta(minutes=1),
    )
    await ingest(container, world.channel.id, human)
    async with container.factory() as s:
        assert (await s.scalars(select(Dialog))).unique().one().ai_paused is True
        pending = await s.scalars(select(Job).where(Job.kind == "reply", Job.status == "pending"))
        assert pending.all() == []


async def test_edited_message_is_stored_but_does_not_trigger_another_reply(container, world):
    await ingest(container, world.channel.id, inbound("курс за 100$?", ext="1"))
    async with transaction(container.factory) as s:  # the reply to the original already ran
        for job in (await s.scalars(select(Job))).all():
            job.status = "done"
    res = await ingest(
        container, world.channel.id, inbound("курс за 200$?", ext="1:e1", is_edit=True)
    )
    assert res.message is not None and not res.reply_queued
    async with container.factory() as s:
        jobs = (await s.scalars(select(Job).where(Job.status == "pending"))).all()
        assert jobs == []                                   # no new reply job
        dialog = (await s.scalars(select(Dialog))).unique().one()
        assert dialog.unread == 1 and dialog.last_message_text == "[изменено] курс за 200$?"
        texts = [m.text for m in (await s.scalars(select(Message).order_by(Message.id))).all()]
        assert texts == ["курс за 100$?", "[изменено] курс за 200$?"]


async def test_edit_of_unknown_dialog_is_ignored(container, world):
    res = await ingest(container, world.channel.id, inbound("x", ext="9:e1", is_edit=True))
    assert res.duplicate
    async with container.factory() as s:
        assert (await s.scalars(select(Message))).all() == []
