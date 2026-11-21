"""`python -m sim.apps.worker`: runs the `agent` queue — replies, understanding attachments,
memory, phone checks —, the `media` queue — generation — and the `channels` queue — Instagram
and WhatsApp (docs/channels.md) — as Procrastinate workers in one process, each with its own
concurrency: a few videos (minutes each) never take the slots of replies. Everything slow lives
here so the telegram process stays responsive.

SIGTERM / SIGINT stop both workers gracefully: each finishes its running jobs (the compose
`stop_grace_period`), cancelling `run_worker_async` is Procrastinate's graceful stop."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sim.apps import run, setup_logging
from sim.config import get_settings
from sim.services.deps import set_deps
from sim.tasks.app import POOL_SIZE
from sim.tasks.queue import AGENT_QUEUE, CHANNELS_QUEUE, MEDIA_QUEUE
from sim.wiring import wire

log = logging.getLogger("sim.worker")


@dataclass(frozen=True)
class WorkerSpec:
    queue: str
    concurrency: int


WORKERS = (
    WorkerSpec(AGENT_QUEUE, 6),     # parallel chats; one chat is serialized by its task lock
    WorkerSpec(MEDIA_QUEUE, 3),     # parallel generations
    # Instagram / WhatsApp sends, receipts, statuses and polls (docs/channels.md). Idle without
    # such channels: the runtime is built by the first job that needs it.
    WorkerSpec(CHANNELS_QUEUE, 3),
)


async def run_workers(app: Any, specs: Sequence[WorkerSpec], stop: asyncio.Event) -> None:
    """One Procrastinate worker per spec until `stop` is set or a worker ends; then all are
    stopped gracefully. A worker that crashed re-raises its error."""
    workers = [asyncio.create_task(
        app.run_worker_async(queues=[spec.queue], concurrency=spec.concurrency,
                             name=f"worker-{spec.queue}", install_signal_handlers=False),
        name=f"worker-{spec.queue}") for spec in specs]
    stopping = asyncio.create_task(stop.wait(), name="worker-stop")
    try:
        await asyncio.wait([*workers, stopping], return_when=asyncio.FIRST_COMPLETED)
    finally:
        stopping.cancel()
        for worker in workers:
            worker.cancel()
        results = await asyncio.gather(*workers, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            raise result


def stop_on_signals(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):     # Windows: no loop signal handlers
            loop.add_signal_handler(sig, stop.set)


async def main() -> None:
    setup_logging()
    wired = wire(get_settings(), pool_size=sum(spec.concurrency for spec in WORKERS) + 2,
                 tasks_pool_size=POOL_SIZE + len(WORKERS))
    set_deps(wired.deps)
    stop = asyncio.Event()
    stop_on_signals(stop)
    try:
        async with wired.tasks_app.open_async():
            log.info("worker started: %s",
                     ", ".join(f"{spec.queue}={spec.concurrency}" for spec in WORKERS))
            await run_workers(wired.tasks_app, WORKERS, stop)
    finally:
        await wired.aclose()


if __name__ == "__main__":
    run(main())
