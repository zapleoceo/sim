"""The worker's tasks (queue `agent`). Each opens its own sessions through the process-wide
Deps (`set_deps` in the entrypoint) and calls one service. Only retryable Broker errors
(PortError) are retried; everything else is recorded by the service and finishes the job.

The tasks that call the Broker run in the idempotency scope of their Procrastinate job
(`in_job_scope`, docs/broker.md §2b): a retry re-attaches to the Broker jobs it already paid for."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

import procrastinate

from sim.call_scope import call_scope
from sim.db.session import transaction
from sim.ports import PortError
from sim.services.attention.check import PhoneCheck
from sim.services.attention.schedule import lost_checks
from sim.services.deps import get_deps
from sim.services.generation import GenerationService
from sim.services.life.planner import LifePlanner
from sim.services.memory.morning import MorningService
from sim.services.memory.night import NightMemory
from sim.services.passport import PassportService
from sim.services.reply import ReplyService
from sim.services.understanding import Understanding
from sim.tasks.queue import (
    AGENT_QUEUE,
    ATTENTION_TICK_CRON,
    ATTENTION_TICK_TASK,
    GENERATE_TASK,
    LIFE_TICK_CRON,
    LIFE_TICK_TASK,
    LOOK_TASK,
    MEMORY_TASK,
    MEMORY_TICK_CRON,
    MEMORY_TICK_TASK,
    NIGHT_TASK,
    PASSPORT_TASK,
    PHONE_CHECK_TASK,
    PLAN_DAY_TASK,
    REPLY_TASK,
    STALLED_CRON,
    STALLED_TASK,
    SUMMARIZE_DAY_TASK,
    UNDERSTAND_TASK,
)
from sim.tasks.stalled import stalled_tick

log = logging.getLogger(__name__)

RETRY = procrastinate.RetryStrategy(max_attempts=3, wait=20, retry_exceptions={PortError})

type Task = Callable[..., Awaitable[None]]


def job_scope_name(job_id: int | None) -> str:
    return f"job-{job_id}"


def in_job_scope(task: Task) -> Task:
    """`task` taking Procrastinate's job context and running in the call scope of its job: the
    job id survives the retries and the re-queue of a stalled job, so every attempt sends the
    same Broker idempotency keys; a deliberate re-run is a new job with new keys."""

    async def run(context: procrastinate.JobContext, *args: Any, **kwargs: Any) -> None:
        with call_scope(job_scope_name(context.job.id)):
            await task(*args, **kwargs)

    run.__name__ = run.__qualname__ = getattr(task, "__name__", run.__name__)
    run.__doc__ = task.__doc__
    return run


async def reply_chat(chat_id: int) -> None:
    result = await ReplyService(get_deps()).run(chat_id)
    log.info("reply chat=%s status=%s run=%s %s", chat_id, result.status, result.run_id,
             result.reason)


async def understand_media(media_id: int) -> None:
    status = await Understanding(get_deps()).understand(media_id)
    log.info("understand media=%s status=%s", media_id, status)


async def run_generation(generation_id: int) -> None:
    status = await GenerationService(get_deps()).run(generation_id)
    log.info("generation %s status=%s", generation_id, status)


async def refresh_memory(chat_id: int) -> None:
    updated = await MorningService(get_deps()).refresh(chat_id)
    log.info("memory chat=%s updated=%s", chat_id, updated)


async def update_passport(chat_id: int) -> None:
    updated = await PassportService(get_deps()).update(chat_id)
    log.info("passport chat=%s updated=%s", chat_id, updated)


async def phone_check(persona_id: int) -> None:
    """The timer of a phone check: its time has come — the check itself runs as
    `look_at_phone`, one at a time per persona (sim/tasks/queue.py)."""
    await get_deps().tasks.look_at_phone(persona_id)


async def look_at_phone(persona_id: int) -> None:
    result = await PhoneCheck(get_deps()).run(persona_id)
    log.info("phone check persona=%s status=%s decisions=%s next=%s", persona_id,
             result.status, len(result.decisions), result.next_check)


async def attention_tick(timestamp: int) -> None:
    """Every 5 minutes: phone checks whose job was lost (a crash, a restart) run again."""
    deps = get_deps()
    async with transaction(deps.sessions) as session:
        lost = await lost_checks(session, deps.clock.now())
    for persona_id in lost:
        await deps.tasks.phone_check(persona_id, 0)


async def plan_day(persona_id: int, day: str, force: bool = False) -> None:
    outcome = await LifePlanner(get_deps()).plan(persona_id, date.fromisoformat(day),
                                                 force=force)
    log.info("life persona=%s day=%s force=%s outcome=%s", persona_id, day, force, outcome)


async def life_tick(timestamp: int) -> None:
    """Every 30 minutes: queue the days that need a plan (docs/persona-life.md)."""
    deps = get_deps()
    for persona_id, day in await LifePlanner(deps).due():
        await deps.tasks.plan_day(persona_id, day.isoformat())


async def night_memory(persona_id: int, day: str) -> None:
    result = await NightMemory(get_deps()).run(persona_id, date.fromisoformat(day))
    log.info("night persona=%s day=%s days=%s passports=%s weeks=%s", persona_id, day,
             result.days, result.passports, result.weeks)


async def summarize_day(chat_id: int, day: str, force: bool = False) -> None:
    result = await NightMemory(get_deps()).rebuild(chat_id, date.fromisoformat(day),
                                                   force=force)
    log.info("diary chat=%s day=%s force=%s outcome=%s", chat_id, day, force, result)


async def memory_tick(timestamp: int) -> None:
    """Every 15 minutes: the personas whose midnight has passed get their night job
    (docs/memory.md)."""
    deps = get_deps()
    for persona_id, day in await NightMemory(deps).due():
        await deps.tasks.night_memory(persona_id, day.isoformat())


def register_agent_tasks(app: procrastinate.App) -> None:
    """Register under the exact shared names (no blueprint namespace prefix)."""
    def broker_task(name: str, task: Task, *,
                    retry: procrastinate.RetryStrategy | bool = False) -> None:
        app.task(name=name, queue=AGENT_QUEUE, retry=retry, pass_context=True)(
            in_job_scope(task))

    broker_task(REPLY_TASK, reply_chat, retry=RETRY)
    broker_task(UNDERSTAND_TASK, understand_media, retry=RETRY)
    broker_task(GENERATE_TASK, run_generation)
    broker_task(MEMORY_TASK, refresh_memory, retry=RETRY)
    broker_task(PASSPORT_TASK, update_passport, retry=RETRY)
    broker_task(PLAN_DAY_TASK, plan_day, retry=RETRY)
    tick = app.task(name=LIFE_TICK_TASK, queue=AGENT_QUEUE)(life_tick)
    app.periodic(cron=LIFE_TICK_CRON, periodic_id="life")(tick)
    app.task(name=PHONE_CHECK_TASK, queue=AGENT_QUEUE)(phone_check)
    broker_task(LOOK_TASK, look_at_phone)
    sweep = app.task(name=ATTENTION_TICK_TASK, queue=AGENT_QUEUE)(attention_tick)
    app.periodic(cron=ATTENTION_TICK_CRON, periodic_id="attention")(sweep)
    broker_task(NIGHT_TASK, night_memory, retry=RETRY)
    broker_task(SUMMARIZE_DAY_TASK, summarize_day, retry=RETRY)
    night = app.task(name=MEMORY_TICK_TASK, queue=AGENT_QUEUE)(memory_tick)
    app.periodic(cron=MEMORY_TICK_CRON, periodic_id="memory")(night)
    stalled = app.task(name=STALLED_TASK, queue=AGENT_QUEUE, pass_context=True)(stalled_tick)
    app.periodic(cron=STALLED_CRON, periodic_id="stalled")(stalled)
