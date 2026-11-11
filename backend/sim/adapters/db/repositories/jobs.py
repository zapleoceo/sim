"""Job queue on the `job` table. Postgres: SELECT ... FOR UPDATE SKIP LOCKED. SQLite ignores the
row lock (single worker in dev/tests) but the guarded UPDATE keeps a claim atomic anyway."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from sim.adapters.db.models import Job
from sim.adapters.db.repositories.base import Repository
from sim.trace_context import current_trace_id

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
            kind=kind, payload=payload, run_at=run_at, dedup_key=dedup_key, max_attempts=max_attempts,
            trace_id=current_trace_id(),
        )
        return await self.add(job)

    async def pending_by_key(self, dedup_key: str) -> Job | None:
        return await self.session.scalar(
            select(Job)
            .where(Job.dedup_key == dedup_key, Job.status == "pending")
            .order_by(Job.id.desc())
            .limit(1)
        )

    async def upsert_pending(
        self, kind: str, payload: dict[str, Any], run_at: datetime, dedup_key: str
    ) -> Job:
        """The one pending job of this key, not earlier than `run_at` (created when missing)."""
        existing = await self.pending_by_key(dedup_key)
        if existing is None:
            return await self.enqueue(kind, payload, run_at, dedup_key=dedup_key)
        existing.run_at = max(existing.run_at, run_at)
        return existing

    async def has_open(self, kind: str, key_prefix: str) -> bool:
        """Is there a pending or running job of this kind whose dedup key starts with the prefix."""
        found = await self.session.scalar(
            select(Job.id)
            .where(
                Job.kind == kind,
                Job.dedup_key.like(f"{key_prefix}%"),
                Job.status.in_(("pending", "running")),
            )
            .limit(1)
        )
        return found is not None

    async def cancel_pending(self, kind: str, dedup_key: str) -> int:
        res = await self.session.execute(
            update(Job)
            .where(Job.kind == kind, Job.dedup_key == dedup_key, Job.status == "pending")
            .values(status="cancelled")
        )
        return res.rowcount or 0  # type: ignore[attr-defined]

    async def claim(
        self, now: datetime, lease_s: int, limit: int = 4, *, exclude_ids: tuple[int, ...] = ()
    ) -> list[Job]:
        """Lease up to `limit` due jobs (pending, or running with an expired lease).

        attempts also fences ownership: increment it on every claim, never reset it on retry.
        """
        due = (Job.status == "pending") & (Job.run_at <= now)
        stale = (Job.status == "running") & (Job.locked_until < now)
        rows = (
            await self.session.scalars(
                select(Job)
                .where(due | stale, Job.id.not_in(exclude_ids))
                .order_by(Job.run_at, Job.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        claimed: list[Job] = []
        for job in rows:
            res = await self.session.execute(
                update(Job)
                .where(
                    Job.id == job.id,
                    Job.status == job.status,
                    Job.attempts == job.attempts,
                    due | stale,
                )
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

    async def complete(self, job_id: int, now: datetime, *, claim_attempt: int) -> bool:
        """Complete only this running claim; a newer claim or terminal state wins.

        Expiry permits a new claim but does not by itself reject the current owner's result.
        This fences queue state, not handler side effects or external calls.
        """
        res = await self.session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "running", Job.attempts == claim_attempt)
            .values(status="done", finished_at=now, locked_until=None, last_error=None)
        )
        return bool(res.rowcount)  # type: ignore[attr-defined]

    async def fail(
        self, job: Job, error: str, now: datetime, *, claim_attempt: int, retry: bool = True
    ) -> bool:
        """Fail only this claim. True means it was changed to terminally failed.

        `claim_attempt` is captured at claim time, never taken from a reloaded job.
        A fenced-out failure returns False so its caller must not run giveup.
        """
        exhausted = (not retry) or claim_attempt >= job.max_attempts
        values: dict[str, Any] = {"last_error": error[:2000], "locked_until": None}
        if exhausted:
            values.update(status="failed", finished_at=now)
        else:
            values.update(status="pending", run_at=now + timedelta(seconds=backoff_s(claim_attempt)))
        res = await self.session.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == "running", Job.attempts == claim_attempt)
            .values(**values)
        )
        return bool(res.rowcount) and exhausted  # type: ignore[attr-defined]
