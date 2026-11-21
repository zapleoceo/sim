"""Rows a failure left half-done AFTER their commit (docs/deploy.md, «Застрявшие строки»).

Committing a row and deferring its job are two steps: the process may die between them, the
defer may fail, the database may fail after a message went out, a worker may be killed in the
middle of a generation. Stalled-job recovery (sim/tasks/stalled.py) sees only jobs; the reaper
looks at the rows. Every two minutes `agent.reap_stuck` (sim/tasks/reaper.py) runs
`Reaper.run`:

- an outgoing message `pending` for more than `PENDING_AFTER` (and less than
  `PENDING_HORIZON`: a day-old message is not sent out of the blue) is queued for sending
  again. Safe: a send job still waiting absorbs it (queueing lock `send-message:<id>`), a
  running one is ahead of it on the chat's `send:` lock and the new job then skips the message
  (no longer pending), and Telegram drops a repeated send by its `random_id`
  (sim/telegram/sending.py);
- a `queued` generation older than `QUEUED_AFTER` is queued again (`generation:<id>` absorbs
  it while its job still waits; a started one is `running`, the new job is a no-op); one that
  did not start within `GENERATION_FRESH` is failed — the context and the status have stopped
  promising it (sim/services/generation_state.py);
- a `running` generation older than its run budget is failed like a generation error:
  `generation_failed` for the operator, the chat's live status stopped, nothing to the client;
- a reply run `running` for more than `REPLY_RUN_BUDGET` is failed (its worker died; the
  recovered job makes a new run)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Generation, Message, ReplyRun
from sim.db.session import transaction
from sim.domain.enums import Direction, GenerationStatus, MessageStatus, RunStatus
from sim.services.deps import Deps
from sim.services.generation import fail_generation
from sim.services.generation_state import GENERATION_FRESH, IN_PROGRESS, run_budget
from sim.services.presence import stop_busy

log = logging.getLogger(__name__)

PENDING_AFTER = timedelta(minutes=5)     # a send normally starts within seconds
PENDING_HORIZON = timedelta(hours=24)
QUEUED_AFTER = timedelta(minutes=5)
# Several model calls of up to 5 minutes each (`SIM_BROKER_POLL_BUDGET_S`); a turn still
# `running` after this is a turn whose worker died.
REPLY_RUN_BUDGET = timedelta(minutes=30)
BATCH = 100                              # rows of each kind per pass


@dataclass(frozen=True)
class Reaped:
    resent: list[int] = field(default_factory=list)              # message ids
    requeued: list[int] = field(default_factory=list)            # generation ids
    failed_generations: list[int] = field(default_factory=list)
    failed_runs: list[int] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.resent or self.requeued or self.failed_generations or self.failed_runs)


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


class Reaper:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    async def run(self) -> Reaped:
        now = self.deps.clock.now()
        async with transaction(self.deps.sessions) as session:
            messages = await _stuck_messages(session, now)
            requeue, failed = await _stuck_generations(session, now)
            runs = await _stuck_runs(session, now)
        tasks = self.deps.tasks
        for message_id, chat_id in messages:
            await tasks.send(message_id, chat_id)
        for generation_id in requeue:
            await tasks.generate(generation_id)
        for chat_id in sorted({chat_id for _, chat_id in failed if chat_id is not None}):
            await stop_busy(tasks, chat_id)
        reaped = Reaped(resent=[message_id for message_id, _ in messages], requeued=requeue,
                        failed_generations=[generation_id for generation_id, _ in failed],
                        failed_runs=runs)
        if not reaped.empty:
            log.warning("reaper: %s", reaped)
        return reaped


async def _stuck_messages(session: AsyncSession, now: datetime) -> list[tuple[int, int]]:
    rows = await session.execute(
        select(Message.id, Message.chat_id)
        .where(Message.direction == Direction.OUT, Message.status == MessageStatus.PENDING,
               Message.created_at < now - PENDING_AFTER,
               Message.created_at >= now - PENDING_HORIZON)
        .order_by(Message.id).limit(BATCH))
    return [(message_id, chat_id) for message_id, chat_id in rows.all()]


FailedGeneration = tuple[int, int | None]     # (generation id, chat id)


async def _stuck_generations(session: AsyncSession,
                             now: datetime) -> tuple[list[int], list[FailedGeneration]]:
    """(generation ids to queue again, failed generations)."""
    rows = await session.scalars(
        select(Generation)
        .where(Generation.status.in_(IN_PROGRESS), Generation.updated_at < now - QUEUED_AFTER)
        .order_by(Generation.id).limit(BATCH)
        .with_for_update(skip_locked=True))
    requeue: list[int] = []
    failed: list[FailedGeneration] = []
    for generation in rows.all():
        age = now - generation.updated_at
        if generation.status == GenerationStatus.QUEUED:
            if age <= GENERATION_FRESH:
                requeue.append(generation.id)
                continue
            error = f"не началась за {_minutes(GENERATION_FRESH)} мин"
        elif age > run_budget(generation.kind):
            error = (f"не закончилась за {_minutes(run_budget(generation.kind))} мин: "
                     "воркер остановился")
        else:
            continue
        if await fail_generation(session, generation, error, now):
            failed.append((generation.id, generation.chat_id))
    return requeue, failed


async def _stuck_runs(session: AsyncSession, now: datetime) -> list[int]:
    runs = await session.scalars(
        select(ReplyRun)
        .where(ReplyRun.status == RunStatus.RUNNING,
               ReplyRun.created_at < now - REPLY_RUN_BUDGET)
        .order_by(ReplyRun.id).limit(BATCH)
        .with_for_update(skip_locked=True))
    failed: list[int] = []
    for run in runs.all():
        run.status = RunStatus.FAILED
        run.error = f"ход оборвался: не закончился за {_minutes(REPLY_RUN_BUDGET)} мин"
        run.finished_at = now
        failed.append(run.id)
    return failed
