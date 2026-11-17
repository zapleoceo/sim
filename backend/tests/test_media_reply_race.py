"""Deterministic media/reply interleavings; fake model and channel only."""
import pytest
from sqlalchemy import select

from sim.adapters.db.models import Dialog, Job, TraceEvent
from sim.adapters.db.repositories.jobs import JobRepo
from sim.services import jobkinds
from sim.worker.loop import Worker
from tests.conftest import decision_json
from tests.test_media import ai_texts, ingest_voice, make_due


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
            assert any((event.detail or {}).get("outcome") == "superseded" for event in events)
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
