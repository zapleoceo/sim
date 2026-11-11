"""Real DB transactions and fake transport: one durable attempt, never exactly-once claims."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from sim.adapters.db.models import Dialog, Job, Message, OutboundSendIntent
from sim.adapters.db.session import transaction
from sim.clock import utcnow
from sim.domain.enums import Delivery
from sim.ports.channel import SendResult
from sim.services.send_receipts import SendReceipts, SendUncertain
from sim.worker.loop import Worker
from tests.conftest import inbound
from tests.test_ingest import ingest


async def queued(container, world):
    await ingest(container, world.channel.id, inbound())
    async with transaction(container.factory) as s:
        dialog = (await s.scalars(select(Dialog))).unique().one()
        msg = await container.outbound(s).queue_ai(dialog, "hello", None)
        # Only exercise this send; leave the inbound reply out of this test.
        for job in (await s.scalars(select(Job))).all():
            if job.kind != "send":
                job.status = "cancelled"
        return msg.id


async def deliver(container, message_id):
    async with transaction(container.factory) as s:
        await container.outbound(s).deliver(message_id)


async def rows(container, message_id):
    async with container.factory() as s:
        return await s.get(Message, message_id), await s.get(OutboundSendIntent, message_id)


async def test_receipt_survives_caller_rollback_and_recovers_without_resend(container, world, connector):
    mid = await queued(container, world)
    with pytest.raises(RuntimeError, match="caller commit"):
        async with transaction(container.factory) as s:
            await container.outbound(s).deliver(mid)
            raise RuntimeError("caller commit failed")
    message, intent = await rows(container, mid)
    assert message.delivery is Delivery.QUEUED
    assert intent.sent_at is not None and intent.external_id == "out-1"
    # A new service/transaction simulates the retry after process restart.
    await deliver(container, mid)
    await deliver(container, mid)
    message, _ = await rows(container, mid)
    assert message.delivery is Delivery.SENT and message.external_id == "out-1"
    assert connector.port.sent == [("chat-1", "hello")]


@pytest.mark.parametrize("when", ["before_transport", "after_transport", "receipt_commit"])
async def test_crash_leaves_permanent_fence(container, world, connector, monkeypatch, when):
    mid = await queued(container, world)
    original_claim = SendReceipts.claim
    original_send = connector.port.send_text

    async def crash_after_claim(self, *args):
        await original_claim(self, *args)
        raise asyncio.CancelledError()

    async def crash_after_send(*args):
        await original_send(*args)
        raise asyncio.CancelledError()

    async def receipt_failed(*args):
        raise OSError("receipt commit failed")

    if when == "before_transport":
        monkeypatch.setattr(SendReceipts, "claim", crash_after_claim)
    elif when == "after_transport":
        monkeypatch.setattr(connector.port, "send_text", crash_after_send)
    else:
        monkeypatch.setattr(SendReceipts, "acknowledge", receipt_failed)
    expected = SendUncertain if when == "receipt_commit" else asyncio.CancelledError
    with pytest.raises(expected):
        await deliver(container, mid)
    _, intent = await rows(container, mid)
    assert intent is not None and intent.sent_at is None
    for _ in range(2):
        with pytest.raises(SendUncertain):
            await deliver(container, mid)
    assert len(connector.port.sent) == (0 if when == "before_transport" else 1)


async def test_failure_to_commit_intent_never_calls_transport(container, world, connector, monkeypatch):
    mid = await queued(container, world)

    async def unavailable(*args):
        raise OSError("intent store unavailable")

    monkeypatch.setattr(SendReceipts, "claim", unavailable)
    with pytest.raises(OSError):
        await deliver(container, mid)
    assert connector.port.sent == []
    assert (await rows(container, mid))[1] is None


@pytest.mark.parametrize("result", ["timeout", "failure"])
async def test_unknown_outcome_is_terminal_and_flags_operator(container, world, connector, monkeypatch, result):
    mid = await queued(container, world)
    calls = []

    async def uncertain(*args):
        calls.append(args)
        if result == "timeout":
            raise TimeoutError("sensitive transport detail")
        return SendResult(ok=False, error="sensitive transport detail")

    monkeypatch.setattr(connector.port, "send_text", uncertain)
    worker = Worker(container)
    await worker.tick()
    async with container.factory() as s:
        job = (await s.scalars(select(Job).where(Job.kind == "send"))).one()
        dialog = (await s.scalars(select(Dialog))).unique().one()
        assert job.status == "failed" and job.attempts == 1
        assert "sensitive" not in job.last_error
        assert dialog.needs_operator
    assert (await rows(container, mid))[0].delivery is Delivery.FAILED
    await worker.tick()
    with pytest.raises(SendUncertain):
        await deliver(container, mid)
    assert len(calls) == 1


async def test_concurrent_claim_race_invokes_transport_once(container, world, connector, monkeypatch):
    mid = await queued(container, world)
    original = SendReceipts.previous
    barrier = asyncio.Event()
    arrivals = 0

    async def both_observe_absence(self, *args):
        nonlocal arrivals
        previous = await original(self, *args)
        assert previous is None
        arrivals += 1
        if arrivals == 2:
            barrier.set()
        await asyncio.wait_for(barrier.wait(), 3)
        return previous

    monkeypatch.setattr(SendReceipts, "previous", both_observe_absence)
    results = await asyncio.gather(deliver(container, mid), deliver(container, mid), return_exceptions=True)
    assert sum(isinstance(r, SendUncertain) for r in results) == 1
    assert results.count(None) == 1
    assert connector.port.sent == [("chat-1", "hello")]


async def test_reclaimed_job_cannot_repeat_inflight_transport(container, world, connector, monkeypatch):
    mid = await queued(container, world)
    started, release = asyncio.Event(), asyncio.Event()
    original = connector.port.send_text
    calls = []

    async def slow_send(*args):
        calls.append(args)
        started.set()
        await release.wait()
        return await original(*args)

    monkeypatch.setattr(connector.port, "send_text", slow_send)
    first, second = Worker(container), Worker(container)
    first_task = asyncio.create_task(first.tick())
    try:
        await asyncio.wait_for(started.wait(), 3)
        async with transaction(container.factory) as s:
            job = (await s.scalars(select(Job).where(Job.kind == "send"))).one()
            job.locked_until = utcnow() - timedelta(seconds=1)
        await second.tick()
    finally:
        release.set()
        await first_task
    assert len(calls) == 1
    assert (await rows(container, mid))[1].sent_at is not None


@pytest.mark.parametrize("field", ["text", "account"])
async def test_receipt_cannot_be_reused_for_changed_payload(container, world, connector, field):
    mid = await queued(container, world)
    with pytest.raises(RuntimeError):
        async with transaction(container.factory) as s:
            await container.outbound(s).deliver(mid)
            raise RuntimeError("rollback")
    async with transaction(container.factory) as s:
        if field == "text":
            (await s.get(Message, mid)).text = "different"
        else:
            dialog = (await s.scalars(select(Dialog))).unique().one()
            dialog.channel.session_enc = container.box.encrypt_json({"token": "changed"})
    with pytest.raises(SendUncertain):
        await deliver(container, mid)
    assert len(connector.port.sent) == 1


async def test_success_without_external_id_still_has_recoverable_receipt(container, world, connector, monkeypatch):
    mid = await queued(container, world)
    calls = []

    async def no_id(*args):
        calls.append(args)
        return SendResult(ok=True)

    monkeypatch.setattr(connector.port, "send_text", no_id)
    with pytest.raises(RuntimeError):
        async with transaction(container.factory) as s:
            await container.outbound(s).deliver(mid)
            raise RuntimeError("rollback")
    await deliver(container, mid)
    assert (await rows(container, mid))[0].delivery is Delivery.SENT
    assert len(calls) == 1


async def test_ai_reply_is_sent_right_away_without_an_artificial_delay(container, world):
    message_id = await queued(container, world)
    async with container.factory() as s:
        message = await s.get(Message, message_id)
        job = (await s.scalars(select(Job).where(Job.kind == "send"))).one()
    assert job.run_at == message.created_at
