"""The worker's tasks (queue `agent`). Each opens its own sessions through the process-wide
Deps (`set_deps` in the entrypoint) and calls one service. Only retryable Broker errors
(PortError) are retried; everything else is recorded by the service and finishes the job."""
from __future__ import annotations

import logging
from datetime import date

import procrastinate

from sim.ports import PortError
from sim.services.deps import get_deps
from sim.services.generation import GenerationService
from sim.services.life.planner import LifePlanner
from sim.services.memory import MemoryService
from sim.services.reply import ReplyService
from sim.services.understanding import Understanding
from sim.tasks.queue import (
    AGENT_QUEUE,
    GENERATE_TASK,
    LIFE_TICK_CRON,
    LIFE_TICK_TASK,
    MEMORY_TASK,
    PLAN_DAY_TASK,
    REPLY_TASK,
    UNDERSTAND_TASK,
)

log = logging.getLogger(__name__)

RETRY = procrastinate.RetryStrategy(max_attempts=3, wait=20, retry_exceptions={PortError})


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
    updated = await MemoryService(get_deps()).refresh(chat_id)
    log.info("memory chat=%s updated=%s", chat_id, updated)


async def plan_day(persona_id: int, day: str, force: bool = False) -> None:
    outcome = await LifePlanner(get_deps()).plan(persona_id, date.fromisoformat(day),
                                                 force=force)
    log.info("life persona=%s day=%s force=%s outcome=%s", persona_id, day, force, outcome)


async def life_tick(timestamp: int) -> None:
    """Every 30 minutes: queue the days that need a plan (docs/persona-life.md)."""
    deps = get_deps()
    for persona_id, day in await LifePlanner(deps).due():
        await deps.tasks.plan_day(persona_id, day.isoformat())


def register_agent_tasks(app: procrastinate.App) -> None:
    """Register under the exact shared names (no blueprint namespace prefix)."""
    app.task(name=REPLY_TASK, queue=AGENT_QUEUE, retry=RETRY)(reply_chat)
    app.task(name=UNDERSTAND_TASK, queue=AGENT_QUEUE, retry=RETRY)(understand_media)
    app.task(name=GENERATE_TASK, queue=AGENT_QUEUE)(run_generation)
    app.task(name=MEMORY_TASK, queue=AGENT_QUEUE, retry=RETRY)(refresh_memory)
    app.task(name=PLAN_DAY_TASK, queue=AGENT_QUEUE, retry=RETRY)(plan_day)
    tick = app.task(name=LIFE_TICK_TASK, queue=AGENT_QUEUE)(life_tick)
    app.periodic(cron=LIFE_TICK_CRON, periodic_id="life")(tick)
