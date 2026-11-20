"""PostgreSQL preview admission and queue isolation without provider calls."""
from __future__ import annotations

import asyncio
from datetime import UTC, timedelta
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import delete, select

from sim.adapters.db.models import AdminUser, ContentPost, Job, Persona, PreviewOperation, Strategy
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.session import transaction
from sim.api.schemas import PreviewIn
from sim.config import Settings
from sim.services import jobkinds, preview_operations
from sim.wiring import build_container


async def preview_operation_checks(url: str) -> None:
    c = build_container(Settings(database_url=url, env="test", preview_async_enabled=True), registry={})
    try:
        async with transaction(c.factory) as s:
            users = [AdminUser(display_name=f"Synthetic Preview owner {i}") for i in range(3)]
            strategy = Strategy(name="Synthetic Preview strategy")
            s.add_all([*users, strategy])
            await s.flush()
            persona = Persona(name="Synthetic Preview persona", strategy_id=strategy.id)
            s.add(persona)
            await s.flush()
            owners, persona_id = [u.id for u in users], persona.id
        now = c.clock()
        prefix = int(now.replace(tzinfo=UTC).timestamp() * 1000)
        keys = [f"{prefix}-{uuid4()}" for _ in range(4)]

        async def enqueue(owner: int, key: str) -> tuple[str, int | str]:
            try:
                async with transaction(c.factory) as s:
                    current = await s.get(Persona, persona_id)
                    row = await preview_operations.start(
                        c, s, owner, current, key, PreviewIn(message="Synthetic request"),
                    )
                    return key, row.job_id
            except preview_operations.PreviewCapacity as exc:
                return key, str(exc)

        with patch.object(preview_operations, "MAX_QUEUE_OPEN", 2):
            same_owner = await asyncio.gather(*(enqueue(owners[0], key) for key in keys[:2]))
            accepted = [(key, job_id) for key, job_id in same_owner if isinstance(job_id, int)]
            [(first_key, first)] = accepted
            assert [status for _, status in same_owner if isinstance(status, str)] == ["owner"]
            different_owners = await asyncio.gather(
                enqueue(owners[1], keys[2]), enqueue(owners[2], keys[3]),
            )
            [second] = [value for _, value in different_owners if isinstance(value, int)]
            assert [status for _, status in different_owners if isinstance(status, str)] == ["queue"]
            assert await enqueue(owners[0], first_key) == (first_key, first)
        ids = {first, second}

        async with transaction(c.factory) as s:
            ordinary = await JobRepo(s).enqueue("preview_smoke_normal", {}, now + timedelta(seconds=1))
            regular_id = ordinary.id
        async with transaction(c.factory) as s:
            unrelated = tuple(await s.scalars(select(Job.id).where(Job.id.not_in(ids | {regular_id}))))
            regular = await JobRepo(s).claim(now + timedelta(seconds=5), 90, exclude_ids=unrelated)
            assert [row.id for row in regular] == [regular_id]

        async def claim_preview() -> int | None:
            async with transaction(c.factory) as s:
                row = await JobRepo(s).claim_preview(now + timedelta(seconds=5), 90)
                return row.id if row else None

        results = await asyncio.gather(claim_preview(), claim_preview())
        assert None in results and any(value in ids for value in results)
        async with transaction(c.factory) as s:
            rows = (await s.scalars(select(PreviewOperation).where(
                PreviewOperation.owner_id.in_(owners),
            ))).all()
            assert {row.job_id for row in rows} == ids
            await s.execute(delete(PreviewOperation).where(PreviewOperation.owner_id.in_(owners)))
            await s.execute(delete(Job).where(Job.id.in_(ids | {regular_id})))
        async with transaction(c.factory) as s:
            post = ContentPost(persona_id=persona_id, caption="Synthetic caption draft")
            s.add(post)
            await s.flush()
            post_id = post.id

        async def enqueue_caption(key: str) -> tuple[str, int]:
            async with transaction(c.factory) as s:
                source = await s.get(ContentPost, post_id)
                row = await preview_operations.start_caption(c, s, owners[0], source, key)
                return row.id, row.job_id

        two_tabs = [f"{prefix}-{uuid4()}" for _ in range(2)]
        first_tab, second_tab = await asyncio.gather(*(enqueue_caption(key) for key in two_tabs))
        assert first_tab == second_tab
        caption_id, caption_job = first_tab
        async with transaction(c.factory) as s:
            for key in two_tabs:
                row = await preview_operations.scoped(s, owners[0], post_id, key, caption=True)
                assert row.id == caption_id
            (await s.get(Job, caption_job)).status = "failed"
        late = f"{prefix}-{uuid4()}"
        assert await enqueue_caption(late) == first_tab
        async with transaction(c.factory) as s:
            await s.execute(delete(PreviewOperation).where(PreviewOperation.id == caption_id))
            await s.execute(delete(Job).where(Job.id == caption_job))
            await s.execute(delete(ContentPost).where(ContentPost.id == post_id))
        async with transaction(c.factory) as s:
            repo = JobRepo(s)
            first_attachment = await repo.enqueue(jobkinds.PREVIEW_ATTACHMENT, {}, now)
            second_attachment = await repo.enqueue(jobkinds.PREVIEW_ATTACHMENT, {}, now)
            speech = await repo.enqueue(jobkinds.PREVIEW_SPEECH, {}, now)
            speech_id = speech.id
            text = await repo.enqueue(jobkinds.PREVIEW_REPLY, {}, now)
            normal = await repo.enqueue("preview_smoke_normal", {}, now)
            file_ids = {first_attachment.id, second_attachment.id}
            text_id, normal_id = text.id, normal.id

        async def claim_attachment() -> int | None:
            async with transaction(c.factory) as s:
                job = await JobRepo(s).claim_preview_attachment(now, 90)
                return job.id if job else None

        attachment_claims = await asyncio.gather(claim_attachment(), claim_attachment())
        assert None in attachment_claims and any(item in file_ids for item in attachment_claims)
        async with transaction(c.factory) as s:
            assert (await JobRepo(s).claim_preview_speech(now, 90)).id == speech_id
        assert await claim_preview() == text_id
        async with transaction(c.factory) as s:
            unrelated = tuple(await s.scalars(select(Job.id).where(Job.id != normal_id)))
            ordinary = await JobRepo(s).claim(now, 90, exclude_ids=unrelated)
            assert [job.id for job in ordinary] == [normal_id]
            await s.execute(delete(Job).where(Job.id.in_(file_ids | {text_id, normal_id, speech_id})))
        print("PostgreSQL preview admission, concurrent caps and ordinary queue isolation PASS")
    finally:
        await c.aclose()
