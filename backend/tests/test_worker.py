from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import select

from sim.adapters.db.models import Channel, Dialog, Job, Message
from sim.adapters.db.repositories.jobs import JobRepo, backoff_s
from sim.adapters.db.session import transaction
from sim.clock import utcnow
from sim.domain.enums import ChannelKind, ClientStatus, Delivery
from sim.services import jobkinds
from sim.worker.handlers import Handler
from sim.worker.loop import Worker
from tests.conftest import FakeConnector, decision_json, inbound
from tests.test_ingest import ingest


async def enqueue(container, kind="x", payload=None, run_at=None, max_attempts=3):
    async with transaction(container.factory) as s:
        job = await JobRepo(s).enqueue(
            kind, payload or {}, run_at or utcnow() - timedelta(seconds=1), max_attempts=max_attempts
        )
        return job.id


async def job_row(container, job_id) -> Job:
    async with container.factory() as s:
        return await s.get(Job, job_id)


async def test_claim_takes_only_due_pending_jobs_and_leases_them(container):
    due = await enqueue(container)
    await enqueue(container, run_at=utcnow() + timedelta(hours=1))
    async with transaction(container.factory) as s:
        claimed = await JobRepo(s).claim(utcnow(), 60)
    assert [j.id for j in claimed] == [due]
    row = await job_row(container, due)
    assert row.status == "running" and row.attempts == 1 and row.locked_until > utcnow()
    async with transaction(container.factory) as s:
        assert await JobRepo(s).claim(utcnow(), 60) == []        # leased: not claimed twice


async def test_expired_lease_is_reclaimed(container):
    job_id = await enqueue(container, run_at=utcnow() - timedelta(hours=1))
    async with transaction(container.factory) as s:
        await JobRepo(s).claim(utcnow() - timedelta(minutes=10), 60)   # lease already expired
    async with transaction(container.factory) as s:
        again = await JobRepo(s).claim(utcnow(), 60)
    assert [j.id for j in again] == [job_id] and again[0].attempts == 2


async def test_success_completes_job(container):
    calls = []

    async def ok(c, s, payload):
        calls.append(payload)

    job_id = await enqueue(container, "ok", {"n": 1})
    worker = Worker(container, {"ok": Handler(ok)})
    assert await worker.tick() == 1
    assert calls == [{"n": 1}] and (await job_row(container, job_id)).status == "done"


async def test_failure_retries_with_backoff_then_fails_and_runs_giveup(container):
    given_up = []

    async def boom(c, s, payload):
        raise RuntimeError("nope")

    async def giveup(c, s, payload):
        given_up.append(payload)

    job_id = await enqueue(container, "boom", {"k": 1}, max_attempts=2)
    worker = Worker(container, {"boom": Handler(boom, giveup)})
    await worker.tick()
    row = await job_row(container, job_id)
    assert row.status == "pending" and row.attempts == 1 and "RuntimeError" in row.last_error
    assert row.run_at > utcnow() + timedelta(seconds=backoff_s(1) - 5)
    assert given_up == []

    async with transaction(container.factory) as s:       # make it due again
        (await s.get(Job, job_id)).run_at = utcnow() - timedelta(seconds=1)
    await worker.tick()
    row = await job_row(container, job_id)
    assert row.status == "failed" and row.attempts == 2 and given_up == [{"k": 1}]


async def test_unknown_kind_fails_without_retry(container):
    job_id = await enqueue(container, "mystery")
    await Worker(container, {}).tick()
    row = await job_row(container, job_id)
    assert row.status == "failed" and "unknown job kind" in row.last_error


def test_backoff_grows_and_is_capped():
    assert [backoff_s(n) for n in (1, 2, 3)] == [30, 60, 120]
    assert backoff_s(50) == 3600


async def test_full_pipeline_reply_then_send(container, world, fake_llm, connector):
    """Inbound -> reply job -> queued AI message -> send job -> delivered through the port."""
    fake_llm.replies = [decision_json(reply="Здравствуйте, Анна!")]
    await ingest(container, world.channel.id, inbound("Привет"))
    async with transaction(container.factory) as s:           # skip the debounce/delay waits
        for job in (await s.scalars(select(Job))).all():
            job.run_at = utcnow() - timedelta(seconds=1)
    worker = Worker(container)
    await worker.tick()                                        # reply
    async with transaction(container.factory) as s:
        for job in (await s.scalars(select(Job).where(Job.kind == jobkinds.SEND))).all():
            job.run_at = utcnow() - timedelta(seconds=1)
    await worker.tick()                                        # send
    assert connector.port.sent == [("chat-1", "Здравствуйте, Анна!")]
    async with container.factory() as s:
        out = (await s.scalars(select(Message).where(Message.direction == "out"))).one()
        assert out.delivery is Delivery.SENT and out.external_id == "out-1"


async def test_send_failure_marks_message_failed_after_retries(container, world, connector):
    await ingest(container, world.channel.id, inbound("hi"))
    connector.port.ok = False
    async with transaction(container.factory) as s:
        dialog = (await s.scalars(select(Dialog))).unique().one()
        msg = await container.outbound(s).queue_ai(dialog, "hello", None)
        msg_id = msg.id
    worker = Worker(container)
    for _ in range(5):
        async with transaction(container.factory) as s:
            for job in (await s.scalars(select(Job).where(Job.kind == "send"))).all():
                job.run_at = utcnow() - timedelta(seconds=1)
        await worker.tick()
    async with container.factory() as s:
        assert (await s.get(Message, msg_id)).delivery is Delivery.FAILED


async def test_followup_ladder_ends_in_no_response(container, world, fake_llm):
    fake_llm.replies = [decision_json(reply="первое")]
    await ingest(container, world.channel.id, inbound("hi"))
    worker = Worker(container)

    async def make_all_due():
        async with transaction(container.factory) as s:
            for job in (await s.scalars(select(Job).where(Job.status == "pending"))).all():
                job.run_at = utcnow() - timedelta(seconds=1)

    await make_all_due()
    await worker.tick()                                        # reply -> schedules followup 0
    for step in range(3):
        fake_llm.replies = [decision_json(reply=f"follow-up {step}")]
        await make_all_due()
        await worker.tick()
    async with container.factory() as s:
        texts = [m.text for m in (await s.scalars(select(Message).order_by(Message.id))).all()]
        assert texts[-3:] == ["follow-up 0", "follow-up 1", "follow-up 2"]
        client = (await s.scalars(select(Dialog))).unique().one().client
        assert client.status is ClientStatus.NO_RESPONSE


async def test_client_reply_cancels_followup(container, world):
    await ingest(container, world.channel.id, inbound("hi", ext="1"))
    worker = Worker(container)
    async with transaction(container.factory) as s:
        for job in (await s.scalars(select(Job))).all():
            job.run_at = utcnow() - timedelta(seconds=1)
    await worker.tick()
    await ingest(container, world.channel.id, inbound("ещё", ext="2"))
    async with container.factory() as s:
        followups = (await s.scalars(select(Job).where(Job.kind == jobkinds.FOLLOWUP))).all()
        assert followups and all(j.status == "cancelled" for j in followups)


async def test_polling_channels_are_ingested(container, world):
    poller = FakeConnector(polling=True)
    poller.port.inbound = [inbound("из instagram", ext="ig-1")]
    container.registry = {world.channel.kind: poller}
    worker = Worker(container)
    assert await worker.poll_channels() == 1
    async with container.factory() as s:
        assert (await s.scalars(select(Message))).one().text == "из instagram"
    assert await worker.poll_channels() == 0


async def test_non_polling_channels_are_not_polled(container, world, connector):
    connector.port.inbound = [inbound("x")]
    assert await Worker(container).poll_channels() == 0


async def test_poll_interval_is_per_connector(container, world):
    """Instagram keeps ig_poll_interval_s; the Telegram userbot polls on its own, shorter one."""
    ig, tg = FakeConnector(ChannelKind.INSTAGRAM, polling=True), FakeConnector(
        ChannelKind.TELEGRAM_USER, polling=True)
    tg.poll_interval_setting = "tg_user_poll_interval_s"
    container.registry = {ChannelKind.INSTAGRAM: ig, ChannelKind.TELEGRAM_USER: tg}
    container.settings = container.settings.model_copy(
        update={"ig_poll_interval_s": 120, "tg_user_poll_interval_s": 20})
    worker = Worker(container)
    assert worker.poll_interval(ChannelKind.INSTAGRAM) == 120
    assert worker.poll_interval(ChannelKind.TELEGRAM_USER) == 20
    worker.maybe_start_poll(1000.0)
    await asyncio.gather(*worker.poll_tasks.values())
    assert set(worker.poll_tasks) == {ChannelKind.INSTAGRAM, ChannelKind.TELEGRAM_USER}
    worker.maybe_start_poll(1030.0)                     # +30 s: only the userbot is due
    assert worker.last_poll == {ChannelKind.INSTAGRAM: 1000.0, ChannelKind.TELEGRAM_USER: 1030.0}
    await asyncio.gather(*worker.poll_tasks.values())


async def test_telegram_user_kind_is_storable(container, world):
    async with transaction(container.factory) as s:
        s.add(Channel(kind=ChannelKind.TELEGRAM_USER, persona_id=world.persona.id, name="acc"))
    async with container.factory() as s:
        kinds = (await s.scalars(select(Channel.kind).order_by(Channel.id))).all()
    assert kinds[-1] is ChannelKind.TELEGRAM_USER


async def test_a_push_event_wakes_the_poll_before_the_interval(container, world):
    """A new Telegram message calls the connector's listener: the kind is polled on the next
    tick, not after the (fallback) interval."""
    tg = FakeConnector(ChannelKind.TELEGRAM_USER, polling=True)
    tg.poll_interval_setting = "tg_user_poll_interval_s"
    wakers = []
    tg.listen = wakers.append
    container.registry = {ChannelKind.TELEGRAM_USER: tg}
    container.settings = container.settings.model_copy(update={"tg_user_poll_interval_s": 90})
    worker = Worker(container)
    assert len(wakers) == 1                              # the worker subscribed once
    worker.maybe_start_poll(1000.0)
    await asyncio.gather(*worker.poll_tasks.values())
    worker.maybe_start_poll(1005.0)                      # +5 s, no event: not due
    assert worker.last_poll[ChannelKind.TELEGRAM_USER] == 1000.0
    wakers[0]()                                          # a new message arrived
    worker.maybe_start_poll(1006.0)
    assert worker.last_poll[ChannelKind.TELEGRAM_USER] == 1006.0 and not worker.wake
    await asyncio.gather(*worker.poll_tasks.values())


def test_the_telegram_user_fallback_sweep_is_slow():
    from sim.config import Settings

    assert Settings.model_fields["tg_user_poll_interval_s"].default == 90
