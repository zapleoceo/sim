"""The worker process runs the `agent` and `media` queues as two Procrastinate workers with
their own concurrency (sim/apps/worker.py): queued videos never take the slots of replies."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from sim.apps.worker import WORKERS, WorkerSpec, run_workers
from sim.tasks.queue import AGENT_QUEUE, MEDIA_QUEUE


class FakeWorkerApp:
    """Only `run_worker_async`: runs until cancelled (Procrastinate's graceful stop)."""

    def __init__(self, crash: str | None = None) -> None:
        self.crash = crash
        self.started: list[dict[str, Any]] = []
        self.stopped: list[str] = []

    async def run_worker_async(self, **options: Any) -> None:
        self.started.append(options)
        [queue] = options["queues"]
        try:
            if queue == self.crash:
                raise RuntimeError(f"{queue} worker crashed")
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.stopped.append(queue)
            raise


async def started(app: FakeWorkerApp, count: int) -> None:
    while len(app.started) < count:
        await asyncio.sleep(0)


async def test_both_queues_run_side_by_side_until_stopped() -> None:
    assert WORKERS == (WorkerSpec(AGENT_QUEUE, 6), WorkerSpec(MEDIA_QUEUE, 3))
    app, stop = FakeWorkerApp(), asyncio.Event()
    running = asyncio.create_task(run_workers(app, WORKERS, stop))
    await started(app, 2)

    stop.set()                                  # SIGTERM
    await running

    assert app.started == [
        {"queues": [AGENT_QUEUE], "concurrency": 6, "name": "worker-agent",
         "install_signal_handlers": False},
        {"queues": [MEDIA_QUEUE], "concurrency": 3, "name": "worker-media",
         "install_signal_handlers": False},
    ]
    assert sorted(app.stopped) == [AGENT_QUEUE, MEDIA_QUEUE]


async def test_a_crashed_worker_stops_the_other_and_is_raised() -> None:
    app = FakeWorkerApp(crash=MEDIA_QUEUE)
    with pytest.raises(RuntimeError, match="media worker crashed"):
        await run_workers(app, WORKERS, asyncio.Event())
    assert app.stopped == [AGENT_QUEUE]
