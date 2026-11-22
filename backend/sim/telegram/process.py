"""The `telegram` process: Telethon sessions, incoming messages, calls and the `telegram` queue.

  run_telegram_process(deps)
    -> stale calls closed -> ChannelRuntime (one client per account) with two hooks:
       Receiver (messages -> Inbox) and CallsAttacher (py-tgcalls -> CallService)
    -> TelegramMessenger and Presence (live chat statuses) registered for the tasks
    -> Procrastinate worker on queue `telegram`
    -> until SIGTERM / SIGINT: the worker finishes its current jobs, clients disconnect.

Nothing here calls a text model: replies, recognition and generation run in the `worker`
process, so a call's audio never waits behind a slow job."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from typing import Any

from sim.telegram.calls import CallsAttacher
from sim.telegram.calls.service import CallService
from sim.telegram.client import ChannelRuntime, Hook
from sim.telegram.receiver import Receiver
from sim.telegram.tasks import QUEUE, get_presence, set_dialer, set_runtime

log = logging.getLogger(__name__)

WORKER_CONCURRENCY = 4
REFRESH_S = 60.0                 # new / re-logged channels are picked up this often


def build_runtime(deps: Any) -> tuple[ChannelRuntime, CallService]:
    """Wires the process from `Deps` (sim.services.deps). With calls on, her own calls go
    through the same attacher (`telegram:place_call`)."""
    from sim.services.context.builder import ContextBuilder
    from sim.services.inbox import Inbox

    inbox = Inbox(deps)
    service = CallService(
        sessions=deps.sessions, settings=deps.settings, clock=deps.clock, live=deps.live,
        inbox=inbox, context=ContextBuilder(deps.clock, deps.settings), tasks=deps.tasks,
        tools=deps.tools, storage=deps.storage)
    hooks: list[Hook] = [Receiver(inbox).attach]
    set_dialer(None)
    if deps.settings.calls_enabled:
        attacher = CallsAttacher(service)
        hooks.append(attacher.attach)
        set_dialer(attacher)
    runtime = ChannelRuntime(deps.sessions, secret_key=deps.settings.secret_key, hooks=hooks)
    return runtime, service


def install_signals(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):   # Windows dev boxes
            loop.add_signal_handler(sig, stop.set)


async def run_telegram_process(deps: Any, *, app: Any = None,
                               stop: asyncio.Event | None = None) -> None:
    """`app`: an OPEN Procrastinate app (the one `deps.tasks` defers with); made and opened
    here when not given."""
    stop = stop or asyncio.Event()
    install_signals(stop)
    if app is None:
        from sim.tasks.app import make_app

        app = make_app(deps.settings)
        async with app.open_async():
            await serve(deps, app, stop)
    else:
        await serve(deps, app, stop)


async def serve(deps: Any, app: Any, stop: asyncio.Event,
                parts: tuple[Any, Any] | None = None) -> None:
    """`parts` = (runtime, call service) for tests; built from `deps` otherwise."""
    runtime, calls = parts or build_runtime(deps)
    closed = await calls.close_stale()
    if closed:
        log.info("closed %s calls interrupted by the last stop", closed)
    set_runtime(runtime, min_interval_s=deps.settings.tg_min_send_interval_s)
    presence = get_presence()
    await runtime.start()
    worker = asyncio.create_task(app.run_worker_async(
        queues=[QUEUE], concurrency=WORKER_CONCURRENCY, install_signal_handlers=False),
        name="telegram-worker")
    try:
        while not stop.is_set() and not worker.done():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), REFRESH_S)
            if not stop.is_set():
                try:
                    await runtime.start()
                except Exception:
                    log.exception("telegram channels not refreshed")
    finally:
        log.info("telegram process stopping")
        worker.cancel()                  # Procrastinate finishes running jobs on cancel
        results = await asyncio.gather(worker, return_exceptions=True)
        if isinstance(results[0], Exception):
            log.error("telegram worker stopped: %r", results[0])
        await presence.close()            # stop re-sending chat statuses
        await runtime.stop()
