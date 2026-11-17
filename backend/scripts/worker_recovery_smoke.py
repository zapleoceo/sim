"""Called only by the disposable PostgreSQL CI gate; no external transport."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select, text

from sim.adapters.db.locks import NS_DIALOG, NS_REPLY, lock, try_lock
from sim.adapters.db.models import Job
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.session import transaction
from sim.clock import utcnow
from sim.services.conversation import ConversationService, REPLY_BUSY_S
from sim.services.job_execution import require_live_execution
from sim.services.send_receipts import SendReceipts, SendUncertain
from sim.worker.handlers import Handler
from sim.worker.loop import Worker


async def reply_pending_contention(factory):
    """Exact revision-retry / failed NS_REPLY interleaving on two real PG transactions."""
    dialog_id, now = 910000008, utcnow()
    key = f"reply:{dialog_id}"
    selected, release, busy_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = JobRepo.pending_by_key
    owner_session, busy_pid = [], []

    async def pause_after_missing(repo, dedup_key):
        result = await original(repo, dedup_key)
        if repo.session is owner_session[0] and not selected.is_set():
            assert result is None
            selected.set()
            await release.wait()
        return result

    async def revision_retry():
        async with transaction(factory) as s:
            owner_session.append(s)
            assert await try_lock(s, NS_REPLY, dialog_id)
            await lock(s, NS_DIALOG, dialog_id)
            service = SimpleNamespace(jobs=JobRepo(s), clock=lambda: now)
            await ConversationService.defer_reply(service, dialog_id, 0)

    async def busy_retry():
        await selected.wait()
        async with transaction(factory) as s:
            busy_pid.append(await s.scalar(text("select pg_backend_pid()")))
            assert not await try_lock(s, NS_REPLY, dialog_id)
            busy_started.set()
            service = SimpleNamespace(jobs=JobRepo(s), clock=lambda: now)
            await ConversationService.defer_reply(service, dialog_id, REPLY_BUSY_S)

    JobRepo.pending_by_key = pause_after_missing
    tasks = [asyncio.create_task(revision_retry()), asyncio.create_task(busy_retry())]
    try:
        await asyncio.wait_for(busy_started.wait(), 10)
        # Old code finishes the competing INSERT. Correct code waits for the dialog lock.
        async def wait_for_race_boundary():
            async with factory() as observer:
                while not tasks[1].done():
                    blocked = await observer.scalar(text(
                        "select cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": busy_pid[0]})
                    if blocked:
                        return
                    await asyncio.sleep(0.01)
        await asyncio.wait_for(wait_for_race_boundary(), 10)
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 10)
        async with transaction(factory) as s:
            pending = list(await s.scalars(select(Job).where(
                Job.dedup_key == key, Job.status == "pending")))
            assert len(pending) == 1, f"duplicate pending reply retries: {len(pending)}"
            assert pending[0].run_at == now + timedelta(seconds=REPLY_BUSY_S)
            pending[0].status = "cancelled"
        print("PostgreSQL revision-retry versus busy-retry: one pending, later schedule PASS")
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        JobRepo.pending_by_key = original


async def worker_recovery_checks(factory):
    await reply_pending_contention(factory)
    now = [utcnow()]
    container = SimpleNamespace(
        factory=factory, clock=lambda: now[0], registry={},
        settings=SimpleNamespace(calls_enabled=False),
    )
    receipts = SendReceipts(factory, container.clock)
    started, release = asyncio.Event(), asyncio.Event()
    sent = []

    async def enqueue_claim():
        async with transaction(factory) as s:
            job = await JobRepo(s).enqueue("recovery_handler", {}, now[0])
            job_id = job.id
        async with transaction(factory) as s:
            [claim] = await JobRepo(s).claim(now[0], 90, 1)
            assert claim.id == job_id
        return claim

    async def slow_handler(c, s, payload):
        await receipts.claim(910000003, "b" * 64)
        started.set()
        await release.wait()
        await require_live_execution()
        sent.append("mock-send")
        await receipts.acknowledge(910000003, "b" * 64, "mock-receipt")

    claim = await enqueue_claim()
    worker = Worker(container, {"recovery_handler": Handler(slow_handler)})
    task = asyncio.create_task(worker.attempt(claim.id, claim.kind, {}, claim.attempts))
    try:
        await asyncio.wait_for(started.wait(), 10)
        now[0] += timedelta(seconds=91)  # fixed clock, no 90-second sleep
        async with transaction(factory) as s:
            assert await JobRepo(s).claim(now[0], 90, 1) == []
        release.set()
        status, error, _ = await asyncio.wait_for(task, 10)
        assert status == "ok" and error is None and sent == ["mock-send"]
        async with factory() as s:
            assert (await s.get(Job, claim.id)).status == "done"
        receipt = await receipts.previous(910000003, "b" * 64)
        assert receipt is not None and receipt.external_id == "mock-receipt"
        status, _, _ = await worker.attempt(claim.id, claim.kind, {}, claim.attempts)
        assert status == "skipped" and sent == ["mock-send"]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)

    # A real connection loss while a remote result is awaited. The committed
    # send intent survives, so another worker cannot blindly repeat the request.
    started, release = asyncio.Event(), asyncio.Event()
    backend_pid = []

    async def lost_handler(c, s, payload):
        backend_pid.append(await s.scalar(text("select pg_backend_pid()")))
        await receipts.claim(910000004, "c" * 64)
        started.set()
        await release.wait()  # simulated external call completes after DB loss
        await require_live_execution()
        sent.append("forbidden-late-effect")

    async def reconcile_handler(c, s, payload):
        # Real persisted receipt, not a synthetic raised exception.
        await receipts.previous(910000004, "c" * 64)
        sent.append("forbidden-resend")

    claim = await enqueue_claim()
    old = Worker(container, {"recovery_handler": Handler(lost_handler)})
    task = asyncio.create_task(old.attempt(claim.id, claim.kind, {}, claim.attempts))
    try:
        await asyncio.wait_for(started.wait(), 10)
        async with transaction(factory) as s:
            assert await s.scalar(text("select pg_terminate_backend(:pid, 5000)"), {"pid": backend_pid[0]})
        now[0] += timedelta(seconds=91)
        async with transaction(factory) as s:
            [next_claim] = await JobRepo(s).claim(now[0], 90, 1)
            assert next_claim.id == claim.id and next_claim.attempts == claim.attempts + 1
        new = Worker(container, {"recovery_handler": Handler(reconcile_handler)})
        status, _, _ = await new.attempt(claim.id, claim.kind, {}, next_claim.attempts)
        assert status == "error"
        release.set()
        await asyncio.wait_for(task, 10)
        async with factory() as s:
            row = await s.get(Job, claim.id)
            assert row.status == "failed" and row.attempts == next_claim.attempts
        try:
            await receipts.previous(910000004, "c" * 64)
        except SendUncertain:
            pass
        else:
            raise AssertionError("uncertain receipt was lost")
        assert sent == ["mock-send"]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    print("Full Worker: live lease expiry, DB loss, late owner and durable uncertain receipt PASS")
