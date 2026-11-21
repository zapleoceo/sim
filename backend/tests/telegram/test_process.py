"""The telegram process loop with fake parts: start, worker on the `telegram` queue, stop."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from sim.telegram import tasks
from sim.telegram.process import serve


class FakeRuntime:
    def __init__(self) -> None:
        self.log: list[str] = []

    async def start(self) -> list[int]:
        self.log.append("start")
        return []

    async def stop(self) -> None:
        self.log.append("stop")

    def client(self, channel_id: int) -> Any:
        raise AssertionError("not used")


class FakeCalls:
    async def close_stale(self) -> int:
        return 2


class FakeApp:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}
        self.running = asyncio.Event()
        self.cancelled = False

    async def run_worker_async(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.running.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def test_serve_runs_the_worker_until_stopped():
    runtime, app, stop = FakeRuntime(), FakeApp(), asyncio.Event()
    deps = SimpleNamespace(settings=SimpleNamespace(tg_min_send_interval_s=0.5))
    task = asyncio.create_task(serve(deps, app, stop, parts=(runtime, FakeCalls())))
    await asyncio.wait_for(app.running.wait(), 1)
    assert app.kwargs == {"queues": ["telegram"], "concurrency": 4,
                          "install_signal_handlers": False}
    assert tasks.get_messenger().interval == 0.5
    stop.set()
    await asyncio.wait_for(task, 1)
    assert app.cancelled
    assert runtime.log == ["start", "stop"]
