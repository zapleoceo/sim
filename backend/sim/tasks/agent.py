"""The worker's tasks (queue `agent`). Each opens its own sessions through the process-wide
Deps (`set_deps` in the entrypoint) and calls one service. Only retryable Broker errors
(PortError) are retried; everything else is recorded by the service and finishes the job."""
from __future__ import annotations

import logging

import procrastinate

from sim.ports import PortError
from sim.services.deps import get_deps
from sim.services.generation import GenerationService
from sim.services.memory import MemoryService
from sim.services.reply import ReplyService
from sim.services.understanding import Understanding
from sim.tasks.queue import (
    AGENT_QUEUE,
    GENERATE_TASK,
    MEMORY_TASK,
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


def register_agent_tasks(app: procrastinate.App) -> None:
    """Register under the exact shared names (no blueprint namespace prefix)."""
    app.task(name=REPLY_TASK, queue=AGENT_QUEUE, retry=RETRY)(reply_chat)
    app.task(name=UNDERSTAND_TASK, queue=AGENT_QUEUE, retry=RETRY)(understand_media)
    app.task(name=GENERATE_TASK, queue=AGENT_QUEUE)(run_generation)
    app.task(name=MEMORY_TASK, queue=AGENT_QUEUE, retry=RETRY)(refresh_memory)
