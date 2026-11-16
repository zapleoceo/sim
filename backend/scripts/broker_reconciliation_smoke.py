"""Full worker/Broker recovery on disposable PostgreSQL; HTTP is entirely synthetic."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import delete, select, text

from sim.adapters.crypto import SecretBox
from sim.adapters.db.models import BrokerRequest, Job
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.session import transaction
from sim.adapters.llm.broker import BrokerLLM
from sim.clock import utcnow
from sim.services.broker_requests import BrokerRequests
from sim.services.job_execution import require_live_execution
from sim.services.send_receipts import SendReceipts
from sim.worker.handlers import Handler
from sim.worker.loop import Worker


async def broker_reconciliation_checks(factory):
    box = SecretBox(Fernet.generate_key().decode())
    for lose_at in ("post", "poll"):
        await recovery_case(factory, box, lose_at)
    # Only synthetic fixtures in this explicitly disposable database are removed
    # so the subsequent migration round-trip cannot discard recovery history.
    async with transaction(factory) as s:
        assert len(list(await s.scalars(select(BrokerRequest)))) == 2
        await s.execute(delete(BrokerRequest))
    print("Full Worker Broker recovery: accepted/unknown POST, DB loss, late owner, idempotency and receipts PASS")


async def recovery_case(factory, box, lose_at):
    now = [utcnow()]
    container = SimpleNamespace(factory=factory, clock=lambda: now[0], registry={},
        settings=SimpleNamespace(calls_enabled=False))
    started, release = asyncio.Event(), asyncio.Event()
    posts, effects, backend_pid = [], [], []
    logical_jobs = {}
    receipts = SendReceipts(factory, container.clock)
    message_id = 920000001 if lose_at == "post" else 920000002

    def make_broker(old):
        async def transport(request):
            if request.method == "POST":
                key = request.headers["Idempotency-Key"]
                posts.append((key, request.content))
                logical_jobs.setdefault(key, 200 + len(logical_jobs))
                if old and lose_at == "post":
                    started.set()
                    await release.wait()  # accepted but the process loses its receipt
                return httpx.Response(200, json={"job_id": logical_jobs[key]})
            if request.url.path == "/v1/jobs":
                key = request.url.params["label.sim_request"]
                return httpx.Response(200, json={"pagination_complete": True, "next_cursor": None,
                    "jobs": [{"job_id": logical_jobs[key], "idempotency_key": key,
                              "labels": {"sim_request": key}}]})
            if old and lose_at == "poll":
                started.set()
                await release.wait()
            return httpx.Response(200, json={"status": "done", "text": "synthetic answer"})

        broker = BrokerLLM("https://broker.invalid", "synthetic-project",
            requests=BrokerRequests(factory, box))
        broker._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        return broker

    old_broker, new_broker = make_broker(True), make_broker(False)

    def handler(broker):
        async def run(c, s, payload):
            backend_pid.append(await s.scalar(text("select pg_backend_pid()")))
            result = await broker.chat([{"role": "user", "content": "synthetic private question"}])
            await require_live_execution()
            if await receipts.previous(message_id, "d" * 64) is None:
                await receipts.claim(message_id, "d" * 64)
                effects.append(result.text)  # mocked channel, no network send
                await receipts.acknowledge(message_id, "d" * 64, "synthetic-receipt")
        return Handler(run)

    async with transaction(factory) as s:
        job = await JobRepo(s).enqueue("broker_recovery", {}, now[0])
        job_id = job.id
    async with transaction(factory) as s:
        [claim] = await JobRepo(s).claim(now[0], 90, 1)
        assert claim.id == job_id
    old = Worker(container, {"broker_recovery": handler(old_broker)})
    task = asyncio.create_task(old.attempt(job_id, claim.kind, {}, claim.attempts))
    try:
        await asyncio.wait_for(started.wait(), 10)
        async with factory() as s:
            row = await s.get(BrokerRequest, (job_id, 0))
            assert row.status == "pending" and "synthetic" not in row.request_cipher
            assert (row.remote_id is None) == (lose_at == "post")
        now[0] += timedelta(seconds=91)
        async with transaction(factory) as s:
            assert await JobRepo(s).claim(now[0], 90, 1) == []  # live owner still holds lock
            assert await s.scalar(text("select pg_terminate_backend(:pid, 5000)"), {"pid": backend_pid[0]})
        async with transaction(factory) as s:
            [new_claim] = await JobRepo(s).claim(now[0], 90, 1)
            assert new_claim.id == job_id and new_claim.attempts == claim.attempts + 1
        new = Worker(container, {"broker_recovery": handler(new_broker)})
        status, error, _ = await asyncio.wait_for(new.attempt(job_id, claim.kind, {}, new_claim.attempts), 10)
        assert status == "ok" and error is None
        release.set()
        await asyncio.wait_for(task, 10)
        assert effects == ["synthetic answer"] and len(logical_jobs) == 1
        assert len(posts) == 1
        assert all(item == posts[0] for item in posts)
        async with factory() as s:
            row = await s.get(BrokerRequest, (job_id, 0))
            assert row.status == "done" and row.generation == new_claim.attempts
            assert (await s.get(Job, job_id)).status == "done"
        assert (await receipts.previous(message_id, "d" * 64)).external_id == "synthetic-receipt"
        status, _, _ = await new.attempt(job_id, claim.kind, {}, new_claim.attempts)
        assert status == "skipped" and len(effects) == 1
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await old_broker.aclose()
        await new_broker.aclose()
