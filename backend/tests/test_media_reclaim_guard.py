"""Expired media claims fail closed without any provider or channel activity."""
from datetime import timedelta

import pytest

from sim.adapters.db.models import Job
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.session import transaction
from sim.clock import utcnow
from sim.worker.handlers import Handler
from sim.worker.loop import Worker


@pytest.mark.parametrize("kind", ["video_note", "persona_media"])
async def test_expired_running_generation_is_quarantined(container, kind):
    now = utcnow()
    expired = now + timedelta(seconds=91)
    payload = {"persona_id": 1, "scene": "synthetic", "provider_job_id": "synthetic-receipt"}
    async with transaction(container.factory) as session:
        job_id = (await JobRepo(session).enqueue(kind, payload, now, max_attempts=1)).id
    async with transaction(container.factory) as session:
        [job] = await JobRepo(session).claim(now, 90)
        assert job.id == job_id and job.attempts == 1
    async with transaction(container.factory) as session:
        assert await JobRepo(session).claim(now + timedelta(seconds=1), 90) == []
    async with transaction(container.factory) as session:
        assert await JobRepo(session).claim(expired, 90) == []
    async with transaction(container.factory) as session:
        job = await session.get(Job, job_id)
        assert job.status == "failed" and job.attempts == 1
        assert job.payload == payload and job.finished_at == expired and job.locked_until is None
        assert job.last_error.startswith("media_generation_outcome_unknown:")
        assert not await JobRepo(session).complete(job_id, expired, claim_attempt=1)
        assert await JobRepo(session).claim(expired + timedelta(hours=1), 90) == []


@pytest.mark.parametrize(("kind", "status"), [
    ("persona_media", "pending"),
    ("video_note", "pending"),
    ("reply", "running"),
    ("send", "running"),
    ("media", "running"),
])
async def test_pending_or_other_job_claim_is_unchanged(container, kind, status):
    now = utcnow()
    async with transaction(container.factory) as session:
        job = await JobRepo(session).enqueue(kind, {}, now)
        job.status = status
        job.attempts = 1
        if status == "running":
            job.locked_until = now - timedelta(seconds=1)
    async with transaction(container.factory) as session:
        [job] = await JobRepo(session).claim(now, 90)
        assert job.kind == kind and job.attempts == 2


@pytest.mark.parametrize("kind", ["video_note", "persona_media"])
async def test_worker_restart_never_dispatches_expired_generation_or_its_giveup(container, kind):
    now = container.clock()
    already_started = {
        "command": "started-before-crash",
        "provider_job_id": "synthetic-accepted-receipt",
        "send_receipt": "synthetic-send-receipt",
    }
    queued = {"command": "still-pending"}
    async with transaction(container.factory) as s:
        queue = JobRepo(s)
        old = await queue.enqueue(kind, already_started, now - timedelta(seconds=2))
        old.status, old.attempts = "running", 1
        old.locked_until = now - timedelta(seconds=1)
        old_id = old.id
        pending = await queue.enqueue(kind, queued, now - timedelta(seconds=1))
        pending.attempts = 1
        pending_id = pending.id

    effects = []

    async def run(c, s, payload):
        effects.extend([("paid_post", payload["command"]), ("send", payload["command"])])

    async def giveup(c, s, payload):
        effects.append(("giveup", payload["command"]))

    worker = Worker(container, {kind: Handler(run, giveup)})
    assert await worker.tick() == 1
    assert await worker.tick() == 0
    status, _, detail = await worker.attempt(old_id, kind, already_started, claim_attempt=1)
    assert status == "skipped" and detail["fenced"]
    assert effects == [("paid_post", "still-pending"), ("send", "still-pending")]
    async with container.factory() as s:
        old, pending = await s.get(Job, old_id), await s.get(Job, pending_id)
        assert old.status == "failed" and old.attempts == 1 and old.payload == already_started
        assert pending.status == "done" and pending.attempts == 2
