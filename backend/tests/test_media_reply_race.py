"""Deterministic media/reply interleavings; fake model and channel only."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from sim.adapters.db.models import Dialog, Job, TraceEvent
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.session import transaction
from sim.clock import utcnow
from sim.domain.enums import ClientStatus
from sim.services import jobkinds
from sim.worker.loop import Worker
from tests.conftest import decision_json, inbound
from tests.test_ingest import ingest
from tests.test_media import ai_texts, ingest_voice, make_due


async def test_reply_enqueue_and_failure_preserve_one_pending_identity(container):
    now, key = utcnow(), "reply:910000009"
    later = now + timedelta(minutes=5)
    async with transaction(container.factory) as s:
        repo = JobRepo(s)
        original = await repo.enqueue("reply", {"dialog_id": 910000009}, now, dedup_key=key)
        [claim] = await repo.claim(now, 90, 1)
        pending = await repo.enqueue("reply", {"dialog_id": 910000009, "delay": {"pause": True}},
                                     later, dedup_key=key)
        same = await repo.enqueue("reply", {}, now, dedup_key=key)
        assert same.id == pending.id and same.run_at == later
        assert not await repo.fail(claim, "synthetic error", now, claim_attempt=claim.attempts)
        await s.flush()
        rows = list(await s.scalars(select(Job).where(Job.dedup_key == key, Job.status == "pending")))
        assert [row.id for row in rows] == [original.id]
        assert rows[0].run_at == later and rows[0].payload["delay"] == {"pause": True}
        assert rows[0].attempts == 1
        assert pending.status == "cancelled"
    async with transaction(container.factory) as s:
        assert await JobRepo(s).claim(now, 90, 1) == []
        [next_claim] = await JobRepo(s).claim(later, 90, 1)
        assert next_claim.id == original.id and next_claim.attempts == 2


async def test_conflicting_executed_reply_retries_fail_closed(container):
    now, key = utcnow(), "reply:910000010"
    async with transaction(container.factory) as s:
        repo = JobRepo(s)
        first = await repo.enqueue("reply", {}, now, dedup_key=key)
        [claim] = await repo.claim(now, 90, 1)
        other = await repo.enqueue("reply", {}, now, dedup_key=key)
        other.attempts = 1  # historical retry may own a different remote receipt
        await s.flush()
        assert await repo.fail(claim, "synthetic", now, claim_attempt=claim.attempts)
        await s.flush()
        assert first.status == other.status == "failed"
        assert "reconciliation" in other.last_error
        assert await repo.claim(now + timedelta(hours=1), 90, 4) == []


async def test_stale_failure_cannot_cancel_fresh_pending(container):
    now, key = utcnow(), "reply:910000011"
    async with transaction(container.factory) as s:
        repo = JobRepo(s)
        old = await repo.enqueue("reply", {}, now, dedup_key=key)
        [claim] = await repo.claim(now, 90, 1)
        fresh = await repo.enqueue("reply", {}, now, dedup_key=key)
        assert not await repo.fail(old, "late", now, claim_attempt=claim.attempts - 1)
        assert fresh.status == "pending" and old.status == "running"


async def test_superseded_followup_does_not_create_customer_reply(
    container, world, fake_llm, connector, monkeypatch,
):
    fake_llm.replies = [decision_json(reply="First answer"), decision_json(reply="Stale followup")]
    incoming = await ingest(container, world.channel.id, inbound("Synthetic question"))
    await make_due(container)
    for _ in range(4):
        await Worker(container).tick()
    assert connector.port.sent == [("chat-1", "First answer")]
    original = fake_llm.chat

    async def change_revision(messages, **kwargs):
        result = await original(messages, **kwargs)
        if not str(kwargs.get("workflow", "")).endswith("guard"):
            async with transaction(container.factory) as s:
                dialog = await s.get(Dialog, incoming.dialog.id)
                dialog.activity_revision += 1
        return result

    monkeypatch.setattr(fake_llm, "chat", change_revision)
    async with transaction(container.factory) as s:
        outcome = await container.conversation(s).run_reply(
            incoming.dialog.id, instruction="Synthetic followup")
        assert outcome.status == "superseded"
        assert await JobRepo(s).pending_by_key(jobkinds.reply_key(incoming.dialog.id)) is None
    assert connector.port.sent == [("chat-1", "First answer")]


@pytest.mark.parametrize("media_first", [False, True])
async def test_transcript_commit_does_not_lose_reply(
    container, world, fake_llm, connector, monkeypatch, media_first,
):
    fake_llm.transcript = "Synthetic current request"
    fake_llm.replies = [decision_json(reply="Current answer")] * 2
    incoming = await ingest_voice(container, world)
    await make_due(container)
    worker = Worker(container)
    claims = await worker.claim_jobs(4)
    media = next(claim for claim in claims if claim.kind == jobkinds.MEDIA)
    reply = next(claim for claim in claims if claim.kind == jobkinds.REPLY)
    original = JobRepo.has_open
    crossed = False

    async def complete_media_between_read_and_check(repo, kind, prefix):
        nonlocal crossed
        if kind == jobkinds.MEDIA and not crossed:
            crossed = True
            old = (await repo.session.get(Dialog, incoming.dialog.id)).activity_revision
            # Real media handler commits in its own transaction after reply cached Dialog.
            await worker.run_job(*media)
            actual = await repo.session.scalar(select(Dialog.activity_revision).where(
                Dialog.id == incoming.dialog.id))
            assert actual > old
            assert (await repo.session.get(Dialog, incoming.dialog.id)).activity_revision == old
        return await original(repo, kind, prefix)

    if media_first:
        await worker.run_job(*media)
    else:
        monkeypatch.setattr(JobRepo, "has_open", complete_media_between_read_and_check)
    await worker.run_job(*reply)
    if not media_first:
        assert crossed
        assert await ai_texts(container) == []  # stale draft must never be queued/sent
        async with container.factory() as session:
            events = list(await session.scalars(select(TraceEvent).where(
                TraceEvent.name == jobkinds.REPLY)))
            assert any(
                (event.detail or {}).get("outcome") == "superseded"
                and (event.detail or {}).get("reason") == "activity_revision_changed"
                for event in events
            )
            pending = list(await session.scalars(select(Job).where(
                Job.kind == jobkinds.REPLY, Job.status == "pending")))
            assert len(pending) == 1, "superseded reply was completed without a replacement"
    # A new worker instance must recover the durable replacement, not an in-memory callback.
    restarted = Worker(container)
    for _ in range(5):
        if await restarted.tick() == 0:
            break
    assert await ai_texts(container) == ["Current answer"]
    assert connector.port.sent == [("chat-1", "Current answer")]
    await restarted.tick()
    assert connector.port.sent == [("chat-1", "Current answer")]


async def test_new_inbound_preserves_later_pending_time_and_restarts_once(
    container, world, fake_llm, connector, monkeypatch,
):
    now = [utcnow()]
    container.clock = lambda: now[0]
    fake_llm.replies = [decision_json(reply="Stale answer"), decision_json(reply="Latest answer")]
    incoming = await ingest(container, world.channel.id, inbound("Original synthetic request"))
    await make_due(container)
    now[0] = utcnow()
    original = fake_llm.chat
    changed = False
    future = now[0] + timedelta(minutes=5)

    async def new_input_after_generation(messages, **kwargs):
        nonlocal changed
        result = await original(messages, **kwargs)
        if not changed and not str(kwargs.get("workflow", "")).endswith("guard"):
            changed = True
            await ingest(container, world.channel.id, inbound("Newer synthetic request", ext="newer"))
            async with transaction(container.factory) as session:
                job = await JobRepo(session).pending_by_key(jobkinds.reply_key(incoming.dialog.id))
                assert job is not None
                job.run_at = future
        return result

    monkeypatch.setattr(fake_llm, "chat", new_input_after_generation)
    assert await Worker(container).tick() == 1
    assert changed and await ai_texts(container) == []
    async with container.factory() as session:
        pending = list(await session.scalars(select(Job).where(
            Job.kind == jobkinds.REPLY, Job.status == "pending")))
        assert len(pending) == 1 and pending[0].run_at == future
    restarted = Worker(container)
    assert await restarted.tick() == 0  # do not bring the later job forward
    now[0] = future + timedelta(seconds=1)
    for _ in range(4):
        await restarted.tick()
    assert connector.port.sent == [("chat-1", "Latest answer")]
    assert await ai_texts(container) == ["Latest answer"]
    assert any("Newer synthetic request" in str(messages) for messages, _ in fake_llm.gen_calls)


@pytest.mark.parametrize("restriction", ["pause", "refused", "stale"])
async def test_revision_recovery_respects_pause_refusal_and_explicit_resume(
    container, world, fake_llm, connector, monkeypatch, restriction,
):
    fake_llm.replies = [decision_json(reply="Stale answer"), decision_json(reply="Resumed answer")]
    occurred = utcnow() - timedelta(days=30) if restriction == "stale" else utcnow()
    incoming = await ingest(container, world.channel.id, inbound("Synthetic request", at=occurred))
    await make_due(container)
    original = fake_llm.chat
    changed = False

    async def restrict_after_generation(messages, **kwargs):
        nonlocal changed
        result = await original(messages, **kwargs)
        if not changed and not str(kwargs.get("workflow", "")).endswith("guard"):
            changed = True
            async with transaction(container.factory) as session:
                dialog = await session.get(Dialog, incoming.dialog.id)
                await container.ai_pause(session).pause(dialog, reason="Synthetic operator action")
                if restriction == "refused":
                    dialog.client.status = ClientStatus.REFUSED
        return result

    monkeypatch.setattr(fake_llm, "chat", restrict_after_generation)
    await Worker(container).tick()
    assert changed and connector.port.sent == [] and await ai_texts(container) == []
    async with transaction(container.factory) as session:
        dialog = await session.get(Dialog, incoming.dialog.id)
        assert dialog.ai_paused
        assert await JobRepo(session).pending_by_key(jobkinds.reply_key(dialog.id)) is None
        # Explicit owner/operator resume uses the existing service and recovery policy.
        recovery = await container.ai_pause(session).resume(dialog)
        assert recovery.status == {"pause": "queued", "refused": "refused", "stale": "stale"}[restriction]
    await make_due(container)
    restarted = Worker(container)
    for _ in range(4):
        await restarted.tick()
    assert connector.port.sent == ([("chat-1", "Resumed answer")] if restriction == "pause" else [])
