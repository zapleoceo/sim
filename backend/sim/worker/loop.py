"""Worker loop: claim and run jobs, poll polling-channels, shut down gracefully."""
from __future__ import annotations

import asyncio
import logging
import signal
from typing import cast

from sim.adapters.db.models import Job
from sim.adapters.db.repositories.catalog import ChannelRepo
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.session import transaction
from sim.config import Settings
from sim.domain.enums import ChannelKind
from sim.ports.channel import PollingChannelPort
from sim.services.conversation import ConversationError
from sim.wiring import Container
from sim.worker.handlers import HANDLERS, Handler

log = logging.getLogger(__name__)

LEASE_MARGIN_S = 300   # HTTP overhead, DB work and clock skew on top of the model calls
CLAIM_BATCH = 4


def lease_seconds(settings: Settings) -> int:
    """How long a claimed job is ours. ConversationService.ask() may make TWO broker calls (one
    retry on bad JSON), each up to the longest poll budget: the lease must outlive both, or
    a second worker re-claims the job while the first is still answering."""
    longest = max(settings.broker_poll_budget_s, settings.broker_subscription_budget_s)
    return int(2 * longest) + LEASE_MARGIN_S


class Worker:
    def __init__(self, container: Container, handlers: dict[str, Handler] | None = None) -> None:
        self.c = container
        self.handlers = handlers if handlers is not None else HANDLERS
        self.stop = asyncio.Event()
        # One poll task per channel kind: a slow Instagram sweep (2-5 s per call) must not hold
        # back the Telegram userbot, which polls every ~20 s.
        self.poll_tasks: dict[ChannelKind, asyncio.Task[None]] = {}
        self.last_poll: dict[ChannelKind, float] = {}

    # ---- jobs ----------------------------------------------------------------------------
    async def tick(self) -> int:
        """Claim due jobs and run them concurrently. Returns how many were run."""
        async with transaction(self.c.factory) as s:
            jobs = await JobRepo(s).claim(
                self.c.clock(), lease_seconds(self.c.settings), CLAIM_BATCH
            )
            snapshot = [(j.id, j.kind, dict(j.payload or {})) for j in jobs]
        if snapshot:
            await asyncio.gather(*(self.run_job(*item) for item in snapshot))
        return len(snapshot)

    async def run_job(self, job_id: int, kind: str, payload: dict) -> None:
        handler = self.handlers.get(kind)
        error: str | None = None
        retry = True
        if handler is None:
            error, retry = f"unknown job kind {kind!r}", False
        else:
            try:
                async with transaction(self.c.factory) as s:
                    await handler.run(self.c, s, payload)
            except ConversationError as exc:
                error, retry = str(exc), False
            except Exception as exc:  # any failure is retried with backoff
                log.warning("job %s (%s) failed: %s", job_id, kind, type(exc).__name__)
                error = f"{type(exc).__name__}: {exc}"
        async with transaction(self.c.factory) as s:
            repo = JobRepo(s)
            if error is None:
                await repo.complete(job_id, self.c.clock())
                return
            job = await s.get(Job, job_id)
            if job is None:
                return
            exhausted = await repo.fail(job, error, self.c.clock(), retry=retry)
        if exhausted and handler is not None and handler.giveup is not None:
            try:
                async with transaction(self.c.factory) as s:
                    await handler.giveup(self.c, s, payload)
            except Exception:
                log.exception("giveup of job %s failed", job_id)

    # ---- polling channels (Instagram, Telegram userbot) ----------------------------------
    def poll_interval(self, kind: ChannelKind) -> float:
        name = getattr(self.c.registry[kind], "poll_interval_setting", "ig_poll_interval_s")
        return float(getattr(self.c.settings, name))

    async def poll_channels(self, only: list[ChannelKind] | None = None) -> int:
        kinds = [k for k, conn in self.c.registry.items()
                 if conn.polling and (only is None or k in only)]
        if not kinds:
            return 0
        async with self.c.factory() as s:
            ids = [ch.id for ch in await ChannelRepo(s).pollable(kinds)]
        total = 0
        for channel_id in ids:
            try:
                total += await self.poll_one(channel_id)
            except Exception as exc:
                log.warning("poll of channel %s failed: %s", channel_id, type(exc).__name__)
                await self.record_poll_error(channel_id, f"{type(exc).__name__}: {exc}"[:300])
        return total

    async def poll_one(self, channel_id: int) -> int:
        async with transaction(self.c.factory) as s:
            channel = await ChannelRepo(s).get(channel_id)
            if channel is None:
                return 0
            connector = self.c.registry[channel.kind]
            session = self.c.box.decrypt_json(channel.session_enc)
            port = cast(PollingChannelPort, connector.build_port(channel.id, session))
            inbound = await port.fetch_inbound()
            ingest = self.c.ingest(s)
            for msg in inbound:
                await ingest.ingest(channel, msg)
            channel.last_error = None
            return len(inbound)

    async def record_poll_error(self, channel_id: int, message: str) -> None:
        async with transaction(self.c.factory) as s:
            channel = await ChannelRepo(s).get(channel_id)
            if channel is not None:
                channel.last_error = message

    async def poll_in_background(self, kind: ChannelKind) -> None:
        try:
            await self.poll_channels([kind])
        except Exception:
            log.exception("channel polling crashed")

    def maybe_start_poll(self, now: float) -> None:
        for kind, conn in self.c.registry.items():
            task = self.poll_tasks.get(kind)
            busy = task is not None and not task.done()
            due = now - self.last_poll.get(kind, float("-inf")) >= self.poll_interval(kind)
            if conn.polling and not busy and due:
                self.last_poll[kind] = now
                self.poll_tasks[kind] = asyncio.create_task(self.poll_in_background(kind))

    # ---- lifecycle -----------------------------------------------------------------------
    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop.set)
            except NotImplementedError:  # Windows event loop
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(self.stop.set))

    async def run(self) -> None:
        self.install_signal_handlers()
        log.info("worker started")
        while not self.stop.is_set():
            try:
                ran = await self.tick()
                self.maybe_start_poll(asyncio.get_running_loop().time())
            except Exception:
                log.exception("worker tick failed")
                ran = 0
            if ran == 0:
                try:
                    await asyncio.wait_for(self.stop.wait(), self.c.settings.worker_tick_s)
                except TimeoutError:
                    pass
        for task in self.poll_tasks.values():
            if not task.done():
                await task
        log.info("worker stopped")
