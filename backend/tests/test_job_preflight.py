from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from sim.adapters.db.models import Job
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.session import transaction
from sim.clock import utcnow
from sim.worker.handlers import Handler
from sim.worker.loop import Worker
from tests.test_job_fencing import expire, state
from tests.test_worker import enqueue, job_row


@pytest.mark.parametrize("status", ["pending", "running", "done", "failed", "cancelled"])
@pytest.mark.parametrize("claim_attempt", [1, 2])
async def test_preflight_matches_running_generation_only(container, status, claim_attempt):
    job_id = await enqueue(container)
    async with transaction(container.factory) as s:
        row = await s.get(Job, job_id)
        row.status, row.attempts = status, 2
        # Expired leases must not themselves revoke an unreclaimed owner's claim.
        row.locked_until = utcnow() - timedelta(seconds=1)
    before = state(await job_row(container, job_id))
    async with container.factory() as s:
        assert await JobRepo(s).is_current_claim(job_id, claim_attempt=claim_attempt) is (
            status == "running" and claim_attempt == 2
        )
    assert state(await job_row(container, job_id)) == before


@pytest.mark.parametrize("newer_status", ["running", "done", "failed"])
async def test_superseded_claim_does_not_start_handler_or_settle(container, newer_status):
    invoked, given_up = [], []

    async def handler(c, s, payload):
        invoked.append(payload)
        raise RuntimeError("must not run")

    async def giveup(c, s, payload):
        given_up.append(payload)

    job_id = await enqueue(container, max_attempts=1)
    worker = Worker(container, {"x": Handler(handler, giveup)})
    [old_claim] = await worker.claim_jobs(1)
    await expire(container, job_id)
    [new_claim] = await worker.claim_jobs(1)
    assert new_claim[3] == 2
    async with transaction(container.factory) as s:
        (await s.get(Job, job_id)).status = newer_status
    before = state(await job_row(container, job_id))
    await worker.run_job(*old_claim)
    assert invoked == given_up == []
    assert state(await job_row(container, job_id)) == before


@pytest.mark.parametrize("status", ["pending", "done", "failed", "cancelled", "missing"])
async def test_inactive_claim_does_not_start_handler(container, status):
    invoked = []

    async def handler(c, s, payload):
        invoked.append(payload)

    job_id = await enqueue(container)
    worker = Worker(container, {"x": Handler(handler)})
    [claim] = await worker.claim_jobs(1)
    async with transaction(container.factory) as s:
        row = await s.get(Job, job_id)
        if status == "missing":
            await s.delete(row)
        else:
            row.status = status
    before = await job_row(container, job_id)
    await worker.run_job(*claim)
    assert invoked == []
    after = await job_row(container, job_id)
    if before is None:
        assert after is None
    else:
        assert state(after) == state(before)


@pytest.mark.parametrize("expired", [False, True])
async def test_current_claim_starts_even_if_expired_but_unreclaimed(container, expired):
    invoked = []

    async def handler(c, s, payload):
        invoked.append(payload)

    job_id = await enqueue(container, payload={"n": 1})
    worker = Worker(container, {"x": Handler(handler)})
    [claim] = await worker.claim_jobs(1)
    if expired:
        await expire(container, job_id)
    await worker.run_job(*claim)
    row = await job_row(container, job_id)
    assert invoked == [{"n": 1}]
    assert row.status == "done" and row.attempts == 1


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_preflight_read_failure_leaves_claim_for_lease_recovery(container, monkeypatch,
                                                                   failure):
    invoked = []

    async def unexpected_handler(c, s, payload):
        invoked.append("handler")

    async def unexpected_giveup(c, s, payload):
        invoked.append("giveup")

    async def unavailable(repo, job_id, *, claim_attempt):
        raise failure("preflight database unavailable")

    job_id = await enqueue(container, max_attempts=1)
    worker = Worker(container, {"x": Handler(unexpected_handler, unexpected_giveup)})
    [claim] = await worker.claim_jobs(1)
    before = state(await job_row(container, job_id))
    monkeypatch.setattr(JobRepo, "is_current_claim", unavailable)
    with pytest.raises(failure, match="preflight database unavailable"):
        await worker.run_job(*claim)
    assert invoked == []
    assert state(await job_row(container, job_id)) == before


async def test_preflight_session_is_closed_before_handler(container, monkeypatch):
    sessions = []
    original_factory = container.factory

    def recording_factory():
        session = original_factory()
        close = session.close
        sessions.append({"session": session, "closed": False})
        record = sessions[-1]

        async def record_close():
            await close()
            record["closed"] = True

        monkeypatch.setattr(session, "close", record_close)
        return session

    async def handler(c, s, payload):
        assert len(sessions) == 2
        assert sessions[0]["closed"] is True
        assert sessions[0]["session"] is not s
        assert sessions[1]["session"] is s

    job_id = await enqueue(container)
    worker = Worker(container, {"x": Handler(handler)})
    [claim] = await worker.claim_jobs(1)
    monkeypatch.setattr(container, "factory", recording_factory)
    await worker.run_job(*claim)
    assert all(record["closed"] for record in sessions)
    assert (await job_row(container, job_id)).status == "done"
