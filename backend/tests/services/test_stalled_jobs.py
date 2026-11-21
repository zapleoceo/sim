"""Jobs a stopped worker left `doing` are recovered every minute (sim/tasks/stalled.py)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import procrastinate
from procrastinate.jobs import Status
from procrastinate.testing import InMemoryConnector

from sim.tasks.agent import register_agent_tasks
from sim.tasks.queue import (
    AGENT_QUEUE,
    GENERATE_TASK,
    LOOK_TASK,
    PASSPORT_TASK,
    PHONE_CHECK_TASK,
    STALLED_TASK,
)
from sim.tasks.stalled import RESTARTABLE, recover_stalled


@dataclass
class Job:
    id: int
    task_name: str


@dataclass
class FakeManager:
    """Only what `recover_stalled` uses of Procrastinate's JobManager."""

    stalled: list[Job]
    asked: list[dict[str, Any]] = field(default_factory=list)
    retried: list[int] = field(default_factory=list)
    finished: list[tuple[int, Status, bool]] = field(default_factory=list)

    async def get_stalled_jobs(self, **filters: Any) -> list[Job]:
        self.asked.append(filters)
        return self.stalled

    async def retry_job(self, job: Job) -> None:
        self.retried.append(job.id)

    async def finish_job(self, job: Job, status: Status, delete_job: bool) -> None:
        self.finished.append((job.id, status, delete_job))


async def test_a_phone_check_left_doing_by_a_deploy_runs_again() -> None:
    # 2026-11-21: the phone check and two passports stayed `doing` after the worker stopped.
    manager = FakeManager([Job(92, PHONE_CHECK_TASK), Job(73, PASSPORT_TASK),
                           Job(80, GENERATE_TASK)])
    retried, failed = await recover_stalled(manager)
    assert retried == [92, 73] == manager.retried
    # A generation could pay and send twice: it is marked failed, its lock released.
    assert failed == [80]
    assert manager.finished == [(80, Status.FAILED, False)]
    # Only the agent queue: Telegram sends belong to the telegram process.
    assert manager.asked == [{"queue": AGENT_QUEUE}]


async def test_nothing_stalled_changes_nothing() -> None:
    manager = FakeManager([])
    assert await recover_stalled(manager) == ([], [])
    assert manager.retried == [] and manager.finished == []


def test_the_recovery_is_registered_every_minute() -> None:
    # A bare app: `make_app` takes the Telegram blueprint, which a process can add only once.
    app = procrastinate.App(connector=InMemoryConnector())
    register_agent_tasks(app)
    assert STALLED_TASK in app.tasks
    assert app.tasks[STALLED_TASK].queue == AGENT_QUEUE
    assert app.tasks[STALLED_TASK].pass_context
    assert any(STALLED_TASK in str(key) for key in app.periodic_registry.periodic_tasks)
    assert GENERATE_TASK not in RESTARTABLE and STALLED_TASK in RESTARTABLE
    # A phone check left `doing` holds its persona's `phone:` lock: it must run again.
    assert {PHONE_CHECK_TASK, LOOK_TASK} <= RESTARTABLE
