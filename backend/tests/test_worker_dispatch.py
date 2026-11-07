from __future__ import annotations

import asyncio
from datetime import timedelta

from sim.adapters.db.models import Job
from sim.adapters.db.session import transaction
from sim.clock import utcnow
from sim.worker.handlers import Handler
from sim.worker.loop import CLAIM_BATCH, Worker
from tests.test_worker import enqueue, job_row


async def test_dispatch_refills_free_slot_without_waiting_for_slow_job(container):
    release = asyncio.Event()
    started = asyncio.Event()
    seen = []

    async def handler(c, s, payload):
        seen.append(payload['n'])
        if payload['n'] == 0:
            started.set()
            await release.wait()

    ids = [await enqueue(container, 'x', {'n': n}) for n in range(6)]
    worker = Worker(container, {'x': Handler(handler)})
    try:
        assert await worker.dispatch() == CLAIM_BATCH
        assert list(worker.job_tasks) == ids[:CLAIM_BATCH]
        await asyncio.wait_for(started.wait(), 2)
        await asyncio.gather(*(t for i, t in worker.job_tasks.items() if i != ids[0]))
        assert not worker.job_tasks[ids[0]].done()
        assert await worker.dispatch() == 2
        assert len(worker.job_tasks) <= CLAIM_BATCH
        await asyncio.gather(*(t for i, t in worker.job_tasks.items() if i != ids[0]))
        assert sorted(seen) == list(range(6))
        assert (await job_row(container, ids[-1])).status == 'done'
    finally:
        release.set()
        await asyncio.gather(*worker.job_tasks.values())


async def test_full_capacity_and_expired_local_lease_do_not_duplicate(container):
    release = asyncio.Event()

    async def handler(c, s, payload):
        await release.wait()

    ids = [await enqueue(container) for _ in range(CLAIM_BATCH + 1)]
    worker = Worker(container, {'x': Handler(handler)})
    try:
        assert await worker.dispatch() == CLAIM_BATCH
        assert await worker.dispatch() == 0
        assert (await job_row(container, ids[-1])).attempts == 0
    finally:
        release.set()
        await asyncio.gather(*worker.job_tasks.values())

    release.clear()
    assert await worker.dispatch() == 1
    try:
        async with transaction(container.factory) as s:
            (await s.get(Job, ids[-1])).locked_until = utcnow() - timedelta(seconds=1)
        new_id = await enqueue(container)
        assert await worker.dispatch() == 1
        assert new_id in worker.job_tasks
        assert (await job_row(container, ids[-1])).attempts == 1
    finally:
        release.set()
        await asyncio.gather(*worker.job_tasks.values())


async def test_run_keeps_polling_and_drains_on_stop(container, monkeypatch):
    started, release, polled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def handler(c, s, payload):
        started.set()
        await release.wait()

    first = await enqueue(container)
    worker = Worker(container, {'x': Handler(handler)})
    monkeypatch.setattr(worker, 'install_signal_handlers', lambda: None)
    container.settings = container.settings.model_copy(update={'worker_tick_s': 0.001})

    def poll(now):
        if started.is_set():
            polled.set()
            worker.stop.set()

    monkeypatch.setattr(worker, 'maybe_start_poll', poll)
    task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(polled.wait(), 2)
        # run is draining, not cancelling the handler, after stop.
        assert not task.done()
        second = await enqueue(container)
        assert await worker.dispatch() == 0
        release.set()
        await asyncio.wait_for(task, 2)
        assert (await job_row(container, first)).status == 'done'
        assert (await job_row(container, second)).status == 'pending'
        assert worker.job_tasks == {}
    finally:
        worker.stop.set()
        release.set()
        await task


async def test_crashed_task_is_observed_and_capacity_recovered(container, monkeypatch):
    async def crash(*args):
        raise RuntimeError('database unavailable')

    await enqueue(container)
    worker = Worker(container)
    errors = []
    monkeypatch.setattr('sim.worker.loop.log.error', lambda *args, **kwargs: errors.append((args, kwargs)))
    monkeypatch.setattr(worker, 'run_job', crash)
    assert await worker.dispatch() == 1
    await asyncio.gather(*worker.job_tasks.values(), return_exceptions=True)
    worker.reap_jobs()
    assert worker.job_tasks == {}
    assert len(errors) == 1
    assert errors[0][1]['exc_info'][0] is RuntimeError


async def test_completion_wakes_refill_before_poll_timeout(container, monkeypatch):
    release = asyncio.Event()
    complete_one = asyncio.Event()
    all_started = asyncio.Event()
    fifth_started = asyncio.Event()
    seen = []

    async def handler(c, s, payload):
        n = payload['n']
        seen.append(n)
        if len(seen) == 4:
            all_started.set()
        if n == 4:
            fifth_started.set()
            worker.stop.set()
        elif n == 1:
            await complete_one.wait()
        else:
            await release.wait()

    for n in range(5):
        await enqueue(container, payload={'n': n})
    container.settings = container.settings.model_copy(update={'worker_tick_s': 3600})
    worker = Worker(container, {'x': Handler(handler)})
    monkeypatch.setattr(worker, 'install_signal_handlers', lambda: None)
    task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(all_started.wait(), 2)
        complete_one.set()
        await asyncio.wait_for(fifth_started.wait(), 2)
        assert not release.is_set()  # Three original jobs remain blocked.
    finally:
        worker.stop.set()
        complete_one.set()
        release.set()
        await asyncio.wait_for(task, 2)
