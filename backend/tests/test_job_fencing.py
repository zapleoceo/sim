"""Deterministic competing-claim regressions on SQLite, not PostgreSQL lock validation."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy.sql.dml import Update

from sim.adapters.db.models import Job
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.session import transaction
from sim.clock import utcnow
from sim.services.conversation import ConversationError
from sim.worker.handlers import Handler
from sim.worker.loop import Worker
from tests.test_worker import enqueue, job_row


def state(job):
    """Include every queue field so stale updates cannot silently erase newer metadata."""
    return {column.name: getattr(job, column.name) for column in Job.__table__.columns}


async def expire(container, job_id):
    async with transaction(container.factory) as s:
        (await s.get(Job, job_id)).locked_until = utcnow() - timedelta(seconds=1)


@pytest.mark.parametrize("initial_status", ["pending", "running"])
async def test_claim_cannot_overwrite_a_competing_claim(container, monkeypatch, initial_status):
    now = utcnow()
    job_id = await enqueue(container, run_at=now - timedelta(hours=1))
    if initial_status == "running":
        async with transaction(container.factory) as s:
            await JobRepo(s).claim(now - timedelta(minutes=2), 60)

    winner = None
    async with transaction(container.factory) as s:
        execute = s.execute

        async def interleave(statement, *args, **kwargs):
            nonlocal winner
            if isinstance(statement, Update):
                assert winner is None
                # Another session wins after our SELECT, before our guarded UPDATE.
                async with transaction(container.factory) as other:
                    [winner] = await JobRepo(other).claim(now, 60)
            return await execute(statement, *args, **kwargs)

        monkeypatch.setattr(s, "execute", interleave)
        assert await JobRepo(s).claim(now, 60) == []
    assert winner is not None
    assert winner.attempts == (1 if initial_status == "pending" else 2)
    assert state(await job_row(container, job_id)) == state(winner)


@pytest.mark.parametrize("initial_status", ["pending", "running"])
async def test_claim_rechecks_due_time_after_selection(container, monkeypatch, initial_status):
    now = utcnow()
    job_id = await enqueue(container, run_at=now - timedelta(hours=1))
    if initial_status == "running":
        async with transaction(container.factory) as s:
            await JobRepo(s).claim(now - timedelta(minutes=2), 60)

    changed = None
    async with transaction(container.factory) as s:
        execute = s.execute

        async def interleave(statement, *args, **kwargs):
            nonlocal changed
            if isinstance(statement, Update):
                assert changed is None
                async with transaction(container.factory) as other:
                    changed = await other.get(Job, job_id)
                    if initial_status == "pending":
                        changed.run_at = now + timedelta(hours=1)
                    else:
                        changed.locked_until = now + timedelta(hours=1)
            return await execute(statement, *args, **kwargs)

        monkeypatch.setattr(s, "execute", interleave)
        assert await JobRepo(s).claim(now, 60) == []
    assert changed is not None
    assert state(await job_row(container, job_id)) == state(changed)


@pytest.mark.parametrize("outcome", ["success", "retry", "exhausted", "conversation_error"])
@pytest.mark.parametrize("newer_status", ["running", "done"])
async def test_stale_worker_cannot_settle_or_giveup_newer_claim(container, outcome, newer_status):
    started, release = asyncio.Event(), asyncio.Event()
    given_up = []

    async def old_handler(c, s, payload):
        started.set()
        await release.wait()
        if outcome == "conversation_error":
            raise ConversationError("invalid conversation")
        if outcome != "success":
            raise RuntimeError("old failure")

    async def giveup(c, s, payload):
        given_up.append(payload)

    async def new_handler(c, s, payload):
        pass

    job_id = await enqueue(container, payload={"n": 1},
                           max_attempts=1 if outcome == "exhausted" else 3)
    old_worker = Worker(container, {"x": Handler(old_handler, giveup)})
    new_worker = Worker(container, {"x": Handler(new_handler)})
    try:
        assert await old_worker.dispatch() == 1
        await asyncio.wait_for(started.wait(), 2)
        await expire(container, job_id)
        [new_claim] = await new_worker.claim_jobs(1)
        assert new_claim[3] == 2
        if newer_status == "done":
            await new_worker.run_job(*new_claim)
        before = state(await job_row(container, job_id))
        release.set()
        await asyncio.wait_for(old_worker.job_tasks[job_id], 2)
        assert given_up == []
        assert state(await job_row(container, job_id)) == before
        assert before["status"] == newer_status
        if newer_status == "running":
            await new_worker.run_job(*new_claim)
        assert (await job_row(container, job_id)).status == "done"
    finally:
        release.set()
        await asyncio.gather(*old_worker.job_tasks.values())


async def test_stale_unknown_kind_does_not_fail_newer_claim(container):
    job_id = await enqueue(container, "unknown")
    worker = Worker(container, {})
    [old_claim] = await worker.claim_jobs(1)
    await expire(container, job_id)
    [new_claim] = await worker.claim_jobs(1)
    before = state(await job_row(container, job_id))
    await worker.run_job(*old_claim)
    assert state(await job_row(container, job_id)) == before
    await worker.run_job(*new_claim)
    row = await job_row(container, job_id)
    assert row.status == "failed" and row.attempts == 2


@pytest.mark.parametrize("status", ["pending", "done", "failed", "cancelled"])
@pytest.mark.parametrize("operation", ["complete", "retry", "fail"])
async def test_non_running_job_cannot_be_settled_again(container, status, operation):
    job_id = await enqueue(container)
    async with transaction(container.factory) as s:
        [claim] = await JobRepo(s).claim(utcnow(), 60)
    async with transaction(container.factory) as s:
        job = await s.get(Job, job_id)
        job.status = status
        job.locked_until = None
        job.last_error = "existing result"
        job.finished_at = utcnow()
    before = state(await job_row(container, job_id))
    async with transaction(container.factory) as s:
        repo = JobRepo(s)
        if operation == "complete":
            applied = await repo.complete(job_id, utcnow(), claim_attempt=claim.attempts)
        else:
            job = await s.get(Job, job_id)
            applied = await repo.fail(job, "late failure", utcnow(),
                                      claim_attempt=claim.attempts, retry=operation == "retry")
        assert applied is False
    assert state(await job_row(container, job_id)) == before


@pytest.mark.parametrize("operation", ["complete", "retry", "fail"])
async def test_expired_but_not_reclaimed_owner_can_still_settle(container, operation):
    job_id = await enqueue(container, run_at=utcnow() - timedelta(hours=1))
    async with transaction(container.factory) as s:
        [claim] = await JobRepo(s).claim(utcnow() - timedelta(minutes=2), 60)
    async with transaction(container.factory) as s:
        repo = JobRepo(s)
        if operation == "complete":
            assert await repo.complete(job_id, utcnow(), claim_attempt=claim.attempts) is True
        else:
            job = await s.get(Job, job_id)
            exhausted = await repo.fail(job, "failed", utcnow(),
                                        claim_attempt=claim.attempts, retry=operation == "retry")
            assert exhausted is (operation == "fail")
    row = await job_row(container, job_id)
    assert row.status == {"complete": "done", "retry": "pending", "fail": "failed"}[operation]
    assert row.attempts == 1 and row.locked_until is None


async def test_missing_job_completion_is_noop(container):
    async with transaction(container.factory) as s:
        assert await JobRepo(s).complete(987, utcnow(), claim_attempt=1) is False


@pytest.mark.parametrize("status", ["pending", "running"])
async def test_claim_generation_fences_same_status_still_due_competitor(container, monkeypatch, status):
    """Status and due checks still match after the rival's claim; only generation fences it."""
    now = utcnow()
    job_id = await enqueue(container, run_at=now - timedelta(hours=1))
    if status == "running":
        async with transaction(container.factory) as s:
            await JobRepo(s).claim(now - timedelta(minutes=3), 60)

    winner = None
    async with transaction(container.factory) as s:
        execute = s.execute

        async def interleave(statement, *args, **kwargs):
            nonlocal winner
            if isinstance(statement, Update):
                assert winner is None
                async with transaction(container.factory) as other:
                    repo = JobRepo(other)
                    # Its lease is already expired when our delayed UPDATE finally runs.
                    [winner] = await repo.claim(now - timedelta(minutes=1), 10)
                    if status == "pending":
                        # An ABA race: pending -> running -> pending, still due at our `now`.
                        assert await repo.fail(
                            winner, "retry", now - timedelta(minutes=1),
                            claim_attempt=winner.attempts,
                        ) is False
            return await execute(statement, *args, **kwargs)

        monkeypatch.setattr(s, "execute", interleave)
        assert await JobRepo(s).claim(now, 60) == []
    assert winner is not None and winner.status == status
    assert state(await job_row(container, job_id)) == state(winner)


async def test_failure_fences_reclaim_between_reload_and_update(container, monkeypatch):
    job_id = await enqueue(container, max_attempts=1)
    worker = Worker(container)
    [old_claim] = await worker.claim_jobs(1)
    await expire(container, job_id)
    winner = None
    async with transaction(container.factory) as s:
        job = await s.get(Job, job_id)
        execute = s.execute

        async def interleave(statement, *args, **kwargs):
            nonlocal winner
            if isinstance(statement, Update):
                assert winner is None
                async with transaction(container.factory) as other:
                    [winner] = await JobRepo(other).claim(utcnow(), 60)
            return await execute(statement, *args, **kwargs)

        monkeypatch.setattr(s, "execute", interleave)
        # Our ORM object still looks owned and exhausted, but the UPDATE must be a no-op.
        assert await JobRepo(s).fail(job, "late terminal failure", utcnow(),
                                     claim_attempt=old_claim[3]) is False
    assert winner is not None and winner.attempts == 2
    assert state(await job_row(container, job_id)) == state(winner)
