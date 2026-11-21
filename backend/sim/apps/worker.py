"""`python -m sim.apps.worker`: runs the `agent` queue — replies, understanding attachments,
generation, memory. Everything slow lives here so the telegram process stays responsive."""
from __future__ import annotations

import logging

from sim.apps import run, setup_logging
from sim.config import get_settings
from sim.services.deps import set_deps
from sim.tasks.queue import AGENT_QUEUE
from sim.wiring import wire

log = logging.getLogger("sim.worker")
CONCURRENCY = 6     # parallel chats; one chat is serialized by its task lock


async def main() -> None:
    setup_logging()
    wired = wire(get_settings(), pool_size=CONCURRENCY + 2)
    set_deps(wired.deps)
    try:
        async with wired.tasks_app.open_async():
            log.info("worker started: queue=%s concurrency=%s", AGENT_QUEUE, CONCURRENCY)
            await wired.tasks_app.run_worker_async(queues=[AGENT_QUEUE], concurrency=CONCURRENCY)
    finally:
        await wired.aclose()


if __name__ == "__main__":
    run(main())
