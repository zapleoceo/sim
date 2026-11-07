"""Offline investigation, deliberately excluded from default test_*.py collection.

These are desired safety properties, not tests that endorse duplicate delivery. Known
gaps use strict xfail, limited to SafetyGap so setup/cleanup failures remain failures.
Run explicitly with offline_run.py tests/lease_risk_reproductions.py -q -rx.
All connector effects are in-memory fakes. No external service is contacted.
"""
from __future__ import annotations

import asyncio
import threading
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.channels.instagram import InstagrapiTransport
from sim.adapters.db.models import Dialog, Job, Message
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.session import transaction
from sim.clock import utcnow
from sim.domain.enums import Delivery
from sim.ports.channel import SendResult
from sim.services import jobkinds
from sim.worker.handlers import Handler
from sim.worker.loop import Worker
from tests.conftest import inbound
from tests.test_ingest import ingest
from tests.test_worker import enqueue, job_row


class SafetyGap(AssertionError):
    """Only the investigated safety invariant may produce an expected failure."""


def require_single_effect(effects):
    if len(effects) != 1:
        raise SafetyGap(f"One external effect required; observed {len(effects)}")


async def queued_send(container, world):
    await ingest(container, world.channel.id, inbound("hello"))
    async with transaction(container.factory) as s:
        # Suppress the unrelated generated reply, so the real SEND handler is isolated.
        await s.execute(update(Job).values(status="cancelled"))
        dialog = (await s.scalars(select(Dialog))).unique().one()
        message = await container.outbound(s).queue_ai(dialog, "reply", None, [0, 0])
        await s.flush()
        job = (await s.scalars(select(Job).where(Job.kind == jobkinds.SEND))).one()
        return job.id, message.id


async def make_retry_due(container, job_id):
    async with transaction(container.factory) as s:
        job = await s.get(Job, job_id)
        assert job.status == "pending"
        job.run_at = utcnow() - timedelta(seconds=1)


async def expire(container, job_id):
    async with transaction(container.factory) as s:
        job = await s.get(Job, job_id)
        assert job.status == "running"
        job.locked_until = utcnow() - timedelta(seconds=1)


@pytest.mark.xfail(strict=True, raises=SafetyGap,
                   reason="A response timeout is retried even if a remote effect happened")
async def test_timeout_after_external_acceptance_does_not_resend(container, world, connector,
                                                               monkeypatch):
    job_id, message_id = await queued_send(container, world)
    effects = []

    async def accepted_then_timeout(thread_id, body):
        effects.append((thread_id, body))
        if len(effects) == 1:
            raise TimeoutError("simulated response loss after remote acceptance")
        return SendResult(ok=True, external_message_id="fake-second")

    monkeypatch.setattr(connector.port, "send_text", accepted_then_timeout)
    worker = Worker(container)
    assert await worker.tick() == 1
    assert (await job_row(container, job_id)).status == "pending"
    async with container.factory() as s:
        assert (await s.get(Message, message_id)).delivery is Delivery.QUEUED
    await make_retry_due(container, job_id)
    assert await worker.tick() == 1
    assert (await job_row(container, job_id)).status == "done"
    require_single_effect(effects)


@pytest.mark.xfail(strict=True, raises=SafetyGap,
                   reason="A successful send followed by commit failure loses its receipt")
async def test_handler_commit_failure_does_not_resend(container, world, connector, monkeypatch):
    job_id, message_id = await queued_send(container, world)
    original_commit = AsyncSession.commit
    rejected = []

    async def reject_sent_receipt_once(session):
        has_sent_message = any(isinstance(row, Message) and row.delivery is Delivery.SENT
                               for row in session.identity_map.values())
        if has_sent_message and not rejected:
            rejected.append(True)
            raise RuntimeError("simulated handler commit failure")
        await original_commit(session)

    monkeypatch.setattr(AsyncSession, "commit", reject_sent_receipt_once)
    worker = Worker(container)
    assert await worker.tick() == 1
    assert rejected == [True]
    assert (await job_row(container, job_id)).status == "pending"
    async with container.factory() as s:
        assert (await s.get(Message, message_id)).delivery is Delivery.QUEUED
    await make_retry_due(container, job_id)
    assert await worker.tick() == 1
    assert (await job_row(container, job_id)).status == "done"
    require_single_effect(connector.port.sent)


@pytest.mark.xfail(strict=True, raises=SafetyGap,
                   reason="Cancellation after external acceptance leaves SEND eligible for reclaim")
async def test_cancelled_inflight_send_is_not_repeated_after_reclaim(container, world, connector,
                                                                  monkeypatch):
    job_id, message_id = await queued_send(container, world)
    accepted, release = asyncio.Event(), asyncio.Event()
    effects = []

    async def accept_then_wait(thread_id, body):
        effects.append((thread_id, body))
        if len(effects) == 1:
            accepted.set()
            await release.wait()
        return SendResult(ok=True, external_message_id="fake-second")

    monkeypatch.setattr(connector.port, "send_text", accept_then_wait)
    worker = Worker(container)
    assert await worker.dispatch() == 1
    task = worker.job_tasks[job_id]
    try:
        await asyncio.wait_for(accepted.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert (await job_row(container, job_id)).status == "running"
    async with container.factory() as s:
        assert (await s.get(Message, message_id)).delivery is Delivery.QUEUED
    await expire(container, job_id)
    assert await Worker(container).tick() == 1
    current = await job_row(container, job_id)
    assert current.status == "done" and current.attempts == 2
    require_single_effect(effects)


async def test_already_superseded_claim_does_not_start_handler(container):
    effects = []

    async def side_effect(c, s, payload):
        effects.append("fake send")

    job_id = await enqueue(container)
    worker = Worker(container, {"x": Handler(side_effect)})
    [old_claim] = await worker.claim_jobs(1)
    await expire(container, job_id)
    [new_claim] = await worker.claim_jobs(1)
    assert old_claim[3] == 1 and new_claim[3] == 2
    await worker.run_job(*old_claim)
    # Preflight skips the old handler without changing the newer claim's queue state.
    assert effects == []
    row = await job_row(container, job_id)
    assert row.status == "running" and row.attempts == 2
    await worker.run_job(*new_claim)
    assert (await job_row(container, job_id)).status == "done"
    require_single_effect(effects)


@pytest.mark.xfail(strict=True, raises=SafetyGap,
                   reason="Ownership can change after a successful preflight read")
async def test_reclaim_after_preflight_cannot_duplicate_effect(container, monkeypatch):
    effects, replacement = [], []

    async def side_effect(c, s, payload):
        effects.append("fake send")

    job_id = await enqueue(container)
    worker = Worker(container, {"x": Handler(side_effect)})
    [old_claim] = await worker.claim_jobs(1)
    original_check = JobRepo.is_current_claim

    async def reclaim_after_read(repo, job_id, *, claim_attempt):
        current = await original_check(repo, job_id, claim_attempt=claim_attempt)
        if claim_attempt == old_claim[3]:
            assert current is True
            await expire(container, job_id)
            replacement.extend(await worker.claim_jobs(1))
        return current

    monkeypatch.setattr(JobRepo, "is_current_claim", reclaim_after_read)
    await worker.run_job(*old_claim)
    assert len(replacement) == 1 and replacement[0][3] == 2
    await worker.run_job(*replacement[0])
    assert (await job_row(container, job_id)).status == "done"
    require_single_effect(effects)


@pytest.mark.xfail(strict=True, raises=SafetyGap,
                   reason="Cancelling asyncio.to_thread does not stop the Instagram send thread")
async def test_cancelled_instagram_transport_cannot_finish_external_send():
    loop = asyncio.get_running_loop()
    entered, completed = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    effects = []
    thread_failures = []

    def fake_direct_send(body, thread_ids):
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(timeout=3):
            thread_failures.append("fake thread was not released")
        else:
            effects.append((thread_ids, body))
        loop.call_soon_threadsafe(completed.set)
        return SimpleNamespace(id="fake-external")

    transport = InstagrapiTransport(
        client_factory=lambda: SimpleNamespace(direct_send=fake_direct_send)
    )
    task = asyncio.create_task(transport.send_direct("1", "reply"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert effects == []
    finally:
        release.set()
        await asyncio.wait_for(completed.wait(), 2)
        await asyncio.gather(task, return_exceptions=True)
    assert thread_failures == []
    if effects:
        raise SafetyGap("Fake Instagram thread completed a send after its coroutine was cancelled")


async def test_sqlite_unrelated_handler_write_blocks_independent_renewal(container):
    """Diagnostic only: SQLite has a DB-wide writer lock, unlike PostgreSQL row locks."""
    job_id = await enqueue(container)
    unrelated_id = await enqueue(container)
    [claim] = await Worker(container).claim_jobs(1)
    assert claim[0] == job_id
    before = await job_row(container, job_id)
    async with container.factory() as writer, container.factory() as heartbeat:
        # Model a handler that flushed any business row before awaiting external I/O.
        await writer.execute(update(Job).where(Job.id == unrelated_id).values(last_error="fake"))
        await heartbeat.execute(text("PRAGMA busy_timeout = 0"))
        try:
            with pytest.raises(OperationalError, match="database is locked"):
                await heartbeat.execute(
                    update(Job).where(Job.id == job_id, Job.status == "running",
                                      Job.attempts == claim[3])
                    .values(locked_until=utcnow() + timedelta(hours=1))
                )
        finally:
            await heartbeat.rollback()
            await writer.rollback()
    after = await job_row(container, job_id)
    assert after.locked_until == before.locked_until and after.attempts == before.attempts
