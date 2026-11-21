"""Jobs a stopped worker left half-done (docs/deploy.md, «Оборванные задачи»).

A deploy stops the worker; a job that was running then stays `doing` for ever: a phone check
that never ends keeps the persona from looking at her phone (its `phone:<persona>` lock holds
back her next checks), and a job holding a `lock` (the passport of a chat) blocks every later
job with the same lock. Found 2026-11-21: Marina did
not answer for 12 minutes after a deploy.

Every minute the worker asks Procrastinate for jobs of the `agent` queue whose worker is gone or
silent (no heartbeat) and:
- queues again the jobs that are safe to run twice: each re-reads the state and does only what
  is still needed (a reply turn, a phone check, a passport, a day plan, …);
- marks the others failed (a generation could pay and send twice): their locks are released and
  the job list shows them."""
from __future__ import annotations

import logging
from typing import Any

import procrastinate
from procrastinate.jobs import Status

from sim.tasks.queue import (
    AGENT_QUEUE,
    ATTENTION_TICK_TASK,
    LIFE_TICK_TASK,
    LOOK_TASK,
    MEMORY_TASK,
    MEMORY_TICK_TASK,
    NIGHT_TASK,
    PASSPORT_TASK,
    PHONE_CHECK_TASK,
    PLAN_DAY_TASK,
    REPLY_TASK,
    STALLED_TASK,
    SUMMARIZE_DAY_TASK,
    UNDERSTAND_TASK,
)

log = logging.getLogger(__name__)

RESTARTABLE = frozenset({
    REPLY_TASK, UNDERSTAND_TASK, MEMORY_TASK, PASSPORT_TASK, PLAN_DAY_TASK, PHONE_CHECK_TASK,
    LOOK_TASK, LIFE_TICK_TASK, ATTENTION_TICK_TASK, STALLED_TASK, NIGHT_TASK, SUMMARIZE_DAY_TASK,
    MEMORY_TICK_TASK,
})


async def recover_stalled(manager: Any) -> tuple[list[int], list[int]]:
    """`manager` is Procrastinate's JobManager. Returns (queued again, marked failed) job ids."""
    retried: list[int] = []
    failed: list[int] = []
    for job in await manager.get_stalled_jobs(queue=AGENT_QUEUE):
        if job.task_name in RESTARTABLE:
            await manager.retry_job(job)
            retried.append(job.id)
        else:
            await manager.finish_job(job, status=Status.FAILED, delete_job=False)
            failed.append(job.id)
    if retried or failed:
        log.warning("stalled jobs: queued again %s, marked failed %s", retried, failed)
    return retried, failed


async def stalled_tick(context: procrastinate.JobContext, timestamp: int) -> None:
    """Every minute: recover the jobs a stopped worker left `doing`."""
    await recover_stalled(context.app.job_manager)
