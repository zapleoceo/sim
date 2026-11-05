"""An AI reply that is already queued must not go out once a human has taken over."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from sim.adapters.db.models import Dialog, Job, Message
from sim.adapters.db.session import transaction
from sim.clock import utcnow
from sim.domain.enums import ClientStatus, Delivery
from sim.worker.loop import Worker
from tests.conftest import OWNER, decision_json, inbound, login
from tests.test_ingest import ingest


async def make_due(c):
    async with transaction(c.factory) as s:
        for j in (await s.scalars(select(Job).where(Job.status == "pending"))).all():
            j.run_at = utcnow() - timedelta(seconds=1)


async def queue_ai_reply(container, world, fake_llm, **decision):
    """Inbound -> reply job run -> AI message waiting in a delayed SEND job."""
    fake_llm.replies = [decision_json(reply="AI answer", **decision)]
    await ingest(container, world.channel.id, inbound("Привет"))
    await make_due(container)
    worker = Worker(container)
    await worker.tick()
    return worker


async def deliveries(container):
    async with container.factory() as s:
        rows = (await s.scalars(select(Message).where(Message.direction == "out"))).all()
        return {m.author.value: m.delivery for m in rows}


async def test_operator_takeover_cancels_queued_ai_reply(container, world, fake_llm, connector):
    worker = await queue_ai_reply(container, world, fake_llm)
    async with transaction(container.factory) as s:
        dialog = (await s.scalars(select(Dialog))).unique().one()
        await container.outbound(s).send_manual(dialog, 1, "Operator here")
    await make_due(container)
    await worker.tick()
    assert connector.port.sent == [("chat-1", "Operator here")]
    assert (await deliveries(container))["ai"] is Delivery.CANCELLED


async def test_owner_phone_message_cancels_queued_ai_reply(container, world, fake_llm, connector):
    worker = await queue_ai_reply(container, world, fake_llm)
    await ingest(container, world.channel.id,
                 inbound("пишу с телефона", ext="own-1", from_me=True))
    await make_due(container)
    await worker.tick()
    assert ("chat-1", "AI answer") not in connector.port.sent
    assert (await deliveries(container))["ai"] is Delivery.CANCELLED


async def test_manual_ai_pause_cancels_queued_ai_reply(api, container, world, fake_llm, connector):
    worker = await queue_ai_reply(container, world, fake_llm)
    csrf = await login(api, *OWNER)
    async with container.factory() as s:
        dialog_id = (await s.scalars(select(Dialog))).unique().one().id
    resp = await api.post(f"/api/dialogs/{dialog_id}/ai", json={"paused": True},
                          headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 200
    await make_due(container)
    await worker.tick()
    assert connector.port.sent == []
    assert (await deliveries(container))["ai"] is Delivery.CANCELLED


async def test_deliver_guard_catches_a_pause_that_left_the_send_job_in_place(
    container, world, fake_llm, connector
):
    """Defence in depth: even with the SEND job still pending, deliver() re-checks."""
    worker = await queue_ai_reply(container, world, fake_llm)
    async with transaction(container.factory) as s:
        (await s.scalars(select(Dialog))).unique().one().ai_paused = True
    await make_due(container)
    await worker.tick()
    assert connector.port.sent == []
    assert (await deliveries(container))["ai"] is Delivery.CANCELLED


async def test_refused_client_gets_no_queued_ai_reply(container, world, fake_llm, connector):
    worker = await queue_ai_reply(container, world, fake_llm)
    async with transaction(container.factory) as s:
        dialog = (await s.scalars(select(Dialog))).unique().one()
        dialog.client.status = ClientStatus.REFUSED
    await make_due(container)
    await worker.tick()
    assert connector.port.sent == []


async def test_farewell_after_refusal_is_still_delivered(container, world, fake_llm, connector):
    """The AI's own last message (client refused / hand-off) is not a stale reply."""
    worker = await queue_ai_reply(container, world, fake_llm, refused=True)
    await make_due(container)
    await worker.tick()
    assert connector.port.sent == [("chat-1", "AI answer")]
