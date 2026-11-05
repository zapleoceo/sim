"""Job queue on the `job` table. Postgres: SELECT ... FOR UPDATE SKIP LOCKED. SQLite ignores the
row lock (single worker in dev/tests) but the guarded UPDATE keeps a claim atomic anyway."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from sim.adapters.db.models import Job
from sim.adapters.db.repositories.base import Repository

BACKOFF_BASE_S = 30
BACKOFF_CAP_S = 3600


def backoff_s(attempts: int) -> int:
    return min(BACKOFF_BASE_S * 2 ** max(attempts - 1, 0), BACKOFF_CAP_S)


class JobRepo(Repository[Job]):
    model = Job

    async def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        run_at: datetime,
        *,
        dedup_key: str | None = None,
        max_attempts: int = 5,
    ) -> Job:
        job = Job(
            kind=kind, payload=payload, run_at=run_at, dedup_key=dedup_key, max_attempts=max_attempts
        )
        return await self.add(job)

    async def pending_by_key(self, dedup_key: str) -> Job | None:
        return await self.session.scalar(
            select(Job)
            .where(Job.dedup_key == dedup_key, Job.status == "pending")
            .order_by(Job.id.desc())
            .limit(1)
        )

    async def cancel_pending(self, kind: str, dedup_key: str) -> int:
        res = await self.session.execute(
            update(Job)
            .where(Job.kind == kind, Job.dedup_key == dedup_key, Job.status == "pending")
            .values(status="cancelled")
        )
        return res.rowcount or 0  # type: ignore[attr-defined]

    async def claim(self, now: datetime, lease_s: int, limit: int = 4) -> list[Job]:
        """Lease up to `limit` due jobs (pending, or running with an expired lease)."""
        due = (Job.status == "pending") & (Job.run_at <= now)
        stale = (Job.status == "running") & (Job.locked_until < now)
        rows = (
            await self.session.scalars(
                select(Job)
                .where(due | stale)
                .order_by(Job.run_at, Job.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        claimed: list[Job] = []
        for job in rows:
            res = await self.session.execute(
                update(Job)
                .where(Job.id == job.id, Job.status == job.status)
                .values(
                    status="running",
                    attempts=Job.attempts + 1,
                    locked_until=now + timedelta(seconds=lease_s),
                )
            )
            if res.rowcount:  # type: ignore[attr-defined]
                await self.session.refresh(job)
                claimed.append(job)
        return claimed

    async def complete(self, job_id: int, now: datetime) -> None:
        await self.session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(status="done", finished_at=now, locked_until=None, last_error=None)
        )

    async def fail(self, job: Job, error: str, now: datetime, *, retry: bool = True) -> bool:
        """Record a failure. Returns True when the job is out of attempts (terminally failed)."""
        exhausted = (not retry) or job.attempts >= job.max_attempts
        values: dict[str, Any] = {"last_error": error[:2000], "locked_until": None}
        if exhausted:
            values.update(status="failed", finished_at=now)
        else:
            values.update(status="pending", run_at=now + timedelta(seconds=backoff_s(job.attempts)))
        await self.session.execute(update(Job).where(Job.id == job.id).values(**values))
        return exhausted
