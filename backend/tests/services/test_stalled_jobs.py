"""Jobs a stopped process left `doing` are recovered every minute (sim/tasks/stalled.py)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import procrastinate
from procrastinate.jobs import Status
from procrastinate.testing import InMemoryConnector

from sim.tasks.agent import register_agent_tasks
from sim.tasks.queue import (
    AGENT_QUEUE,
    CHANNELS_QUEUE,
    CHANNELS_SEND_TASK,
    CHAT_ACTION_TASK,
    GENERATE_TASK,
    LOOK_TASK,
    MARK_READ_TASK,
    MEDIA_QUEUE,
    PASSPORT_TASK,
    PHONE_CHECK_TASK,
    REAP_TASK,
    SEND_TASK,
    STALLED_TASK,
    TELEGRAM_QUEUE,
)
from sim.tasks.stalled import RESTARTABLE, recover_stalled


@dataclass
class Job:
    id: int
    task_name: str


@dataclass
class FakeManager:
    """Only what `recover_stalled` uses of Procrastinate's JobManager."""

    stalled: dict[str, list[Job]]
    asked: list[dict[str, Any]] = field(default_factory=list)
    retried: list[int] = field(default_factory=list)
    finished: list[tuple[int, Status, bool]] = field(default_factory=list)

    async def get_stalled_jobs(self, **filters: Any) -> list[Job]:
        self.asked.append(filters)
        return self.stalled.get(filters["queue"], [])

    async def retry_job(self, job: Job) -> None:
        self.retried.append(job.id)

    async def finish_job(self, job: Job, status: Status, delete_job: bool) -> None:
        self.finished.append((job.id, status, delete_job))


async def test_a_phone_check_left_doing_by_a_deploy_runs_again() -> None:
    # 2026-11-21: the phone check and two passports stayed `doing` after the worker stopped.
    manager = FakeManager({AGENT_QUEUE: [Job(92, PHONE_CHECK_TASK), Job(73, PASSPORT_TASK)],
                           MEDIA_QUEUE: [Job(80, GENERATE_TASK)]})
    retried, failed = await recover_stalled(manager)
    assert retried == [92, 73] == manager.retried
    # A generation could pay and send twice: it is marked failed, its lock released.
    assert failed == [80]
    assert manager.finished == [(80, Status.FAILED, False)]
    assert manager.asked == [{"queue": AGENT_QUEUE}, {"queue": MEDIA_QUEUE},
                             {"queue": TELEGRAM_QUEUE}, {"queue": CHANNELS_QUEUE}]


async def test_a_send_killed_mid_way_runs_again() -> None:
    # The telegram process was killed while sending: the job holds `send:<chat>` and every
    # later message of that chat would wait for ever. A repeat is safe: `deliver` skips a sent
    # message and the send carries the same random_id.
    manager = FakeManager({TELEGRAM_QUEUE: [Job(5, SEND_TASK), Job(6, MARK_READ_TASK),
                                            Job(7, CHAT_ACTION_TASK)]})
    retried, failed = await recover_stalled(manager)
    assert retried == [5, 6, 7] and failed == []
    assert manager.finished == []


async def test_an_instagram_or_whatsapp_send_killed_mid_way_runs_again() -> None:
    # The `channels` queue lives in the worker: a deploy can kill a send too. The repeat is
    # safe: the send claims its message and skips it once it is sent (docs/channels.md).
    manager = FakeManager({CHANNELS_QUEUE: [Job(8, CHANNELS_SEND_TASK)]})
    assert await recover_stalled(manager) == ([8], [])


async def test_nothing_stalled_changes_nothing() -> None:
    manager = FakeManager({})
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
    assert {SEND_TASK, MARK_READ_TASK, CHAT_ACTION_TASK, REAP_TASK} <= RESTARTABLE


def test_generation_has_its_own_queue_and_the_reaper_runs_every_two_minutes() -> None:
    app = procrastinate.App(connector=InMemoryConnector())
    register_agent_tasks(app)
    assert app.tasks[GENERATE_TASK].queue == MEDIA_QUEUE
    assert app.tasks[REAP_TASK].queue == AGENT_QUEUE
    assert any(REAP_TASK in str(key) for key in app.periodic_registry.periodic_tasks)
