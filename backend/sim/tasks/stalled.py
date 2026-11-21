"""Jobs a stopped process left half-done (docs/deploy.md, «Оборванные задачи»).

A deploy stops the worker; a job that was running then stays `doing` for ever: a phone check
that never ends keeps the persona from looking at her phone (its `phone:<persona>` lock holds
back her next checks), and a job holding a `lock` (the passport of a chat) blocks every later
job with the same lock. Found 2026-11-21: Marina did
not answer for 12 minutes after a deploy. The same holds for the telegram process: a send killed
mid-way keeps `send:<chat>` and every later message of that chat waits behind it.

Every minute the worker asks Procrastinate for jobs of the `agent`, `media` and `telegram`
queues whose worker is gone or silent (no heartbeat) — the job manager sees every queue, so the
worker recovers the telegram process's jobs too — and:
- queues again the jobs that are safe to run twice: each re-reads the state and does only what
  is still needed (a reply turn, a phone check, a passport, a day plan, …). A send is one of
  them: `deliver` skips a message that is no longer pending, and Telegram drops a repeated send
  by its `random_id` derived from the message id (sim/telegram/sending.py);
- marks the others failed (a generation could pay and send twice): their locks are released and
  the job list shows them; the generation row itself is failed by the reaper
  (sim/services/reaper.py) once it outlives its budget."""
from __future__ import annotations

import logging
from typing import Any

import procrastinate
from procrastinate.jobs import Status

from sim.tasks.queue import (
    AGENT_QUEUE,
    ATTENTION_TICK_TASK,
    CHAT_ACTION_TASK,
    LIFE_TICK_TASK,
    LOOK_TASK,
    MARK_READ_TASK,
    MEDIA_QUEUE,
    MEMORY_TASK,
    MEMORY_TICK_TASK,
    NIGHT_TASK,
    PASSPORT_TASK,
    PHONE_CHECK_TASK,
    PLAN_DAY_TASK,
    REAP_TASK,
    REPLY_TASK,
    SEND_TASK,
    STALLED_TASK,
    SUMMARIZE_DAY_TASK,
    TELEGRAM_QUEUE,
    UNDERSTAND_TASK,
)

log = logging.getLogger(__name__)

RECOVERED_QUEUES = (AGENT_QUEUE, MEDIA_QUEUE, TELEGRAM_QUEUE)
RESTARTABLE = frozenset({
    REPLY_TASK, UNDERSTAND_TASK, MEMORY_TASK, PASSPORT_TASK, PLAN_DAY_TASK, PHONE_CHECK_TASK,
    LOOK_TASK, LIFE_TICK_TASK, ATTENTION_TICK_TASK, STALLED_TASK, NIGHT_TASK, SUMMARIZE_DAY_TASK,
    MEMORY_TICK_TASK, REAP_TASK,
    SEND_TASK, MARK_READ_TASK, CHAT_ACTION_TASK,
})


async def recover_stalled(manager: Any) -> tuple[list[int], list[int]]:
    """`manager` is Procrastinate's JobManager. Returns (queued again, marked failed) job ids."""
    retried: list[int] = []
    failed: list[int] = []
    for queue in RECOVERED_QUEUES:
        for job in await manager.get_stalled_jobs(queue=queue):
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
    """Every minute: recover the jobs a stopped process left `doing`."""
    await recover_stalled(context.app.job_manager)
