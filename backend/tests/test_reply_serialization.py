"""One reply at a time per dialog; a reply made from stale context is dropped."""
from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import select

from sim.adapters.db.locks import NS_REPLY, try_lock
from sim.adapters.db.models import Job, Message
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.session import transaction
from sim.clock import utcnow
from sim.config import Settings
from sim.services import jobkinds
from sim.worker.loop import Worker, lease_seconds
from tests.conftest import decision_json, inbound
from tests.test_ingest import ingest


async def make_due(c, kind=jobkinds.REPLY):
    async with transaction(c.factory) as s:
        q = select(Job).where(Job.status == "pending", Job.kind == kind)
        for j in (await s.scalars(q)).all():
            j.run_at = utcnow() - timedelta(seconds=1)


async def ai_texts(container):
    async with container.factory() as s:
        return [m.text for m in (await s.scalars(select(Message).where(Message.author == "ai"))).all()]


def gate_llm(fake_llm):
    """The first model call blocks until `release` is set (a slow broker)."""
    entered, release = asyncio.Event(), asyncio.Event()
    original = fake_llm.chat
    first = True
    fake_llm.started = 0

    async def chat(*args, **kwargs):
        nonlocal first
        fake_llm.started += 1
        if first:
            first = False
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    fake_llm.chat = chat
    return entered, release


async def claim_reply(container):
    await make_due(container)
    async with transaction(container.factory) as s:
        job = (await JobRepo(s).claim(utcnow(), 900))[0]
        return job.id, dict(job.payload)


async def test_client_message_during_a_running_reply_yields_one_reply_with_full_context(
    container, world, fake_llm, connector
):
    fake_llm.replies = [decision_json(reply="A1 (stale)"), decision_json(reply="A2 (full)")]
    entered, release = gate_llm(fake_llm)
    await ingest(container, world.channel.id, inbound("one", ext="m1"))
    job_id, payload = await claim_reply(container)
    worker = Worker(container)
    first = asyncio.create_task(worker.run_job(job_id, jobkinds.REPLY, payload))
    await entered.wait()                                   # job 1 is inside the model call
    await ingest(container, world.channel.id, inbound("two", ext="m2"))
    await make_due(container)
    await worker.tick()                                    # job 2 is due now
    assert fake_llm.started == 1                           # ...but did not start a parallel call
    release.set()
    await first
    assert await ai_texts(container) == []                 # job 1's answer was dropped as stale
    await make_due(container)
    await worker.tick()                                    # job 2, with both messages
    assert await ai_texts(container) == ["A2 (full)"]
    sent_prompt = fake_llm.gen_calls[1][0]
    users = " ".join(m["content"] for m in sent_prompt if m["role"] == "user")
    assert "one" in users and "two" in users


async def test_reply_is_dropped_when_the_client_wrote_after_the_prompt_was_built(
    container, world, fake_llm
):
    """No overlap at all: job 1 finishes first, yet its answer predates message 2."""
    fake_llm.replies = [decision_json(reply="stale"), decision_json(reply="fresh")]
    entered, release = gate_llm(fake_llm)
    await ingest(container, world.channel.id, inbound("one", ext="m1"))
    job_id, payload = await claim_reply(container)
    worker = Worker(container)
    first = asyncio.create_task(worker.run_job(job_id, jobkinds.REPLY, payload))
    await entered.wait()
    await ingest(container, world.channel.id, inbound("two", ext="m2"))
    release.set()
    await first
    assert await ai_texts(container) == []
    async with container.factory() as s:                   # a reply job is waiting for m2
        pending = (await s.scalars(select(Job).where(Job.kind == jobkinds.REPLY,
                                                     Job.status == "pending"))).all()
        assert len(pending) == 1
    await make_due(container)
    await worker.tick()
    assert await ai_texts(container) == ["fresh"]


async def test_try_lock_is_exclusive_until_the_transaction_ends(container):
    async with transaction(container.factory) as a:
        assert await try_lock(a, NS_REPLY, 7) is True
        async with transaction(container.factory) as b:
            assert await try_lock(b, NS_REPLY, 7) is False
            assert await try_lock(b, NS_REPLY, 8) is True      # another dialog is independent
    async with transaction(container.factory) as c:
        assert await try_lock(c, NS_REPLY, 7) is True           # released on commit


def test_lease_outlives_two_subscription_budgets():
    s = Settings(broker_subscription_budget_s=600, broker_poll_budget_s=180, _env_file=None)
    assert lease_seconds(s) > 2 * 600
    bigger = Settings(broker_subscription_budget_s=900, _env_file=None)
    assert lease_seconds(bigger) > 2 * 900                     # derived, not a constant
