"""Worker loop: claim and run jobs, poll polling-channels, shut down gracefully."""
from __future__ import annotations

import asyncio
import logging
import signal
import time
from datetime import datetime
from typing import Any, NamedTuple, cast

from sim.adapters.db.models import Job
from sim.adapters.db.repositories.catalog import ChannelRepo
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.session import transaction
from sim.config import Settings
from sim.domain.enums import ChannelKind
from sim.ports.channel import PollingChannelPort
from sim.services.conversation import ConversationError
from sim.services import tracing
from sim.services.send_receipts import SendUncertain
from sim.services.tracing import TraceContext, trace_scope
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
    # ...plus up to four guard calls (verdict + same-model fallback, twice after a rewrite),
    # which run on non-subscription rotations.
    return int(2 * longest + 4 * settings.broker_poll_budget_s) + LEASE_MARGIN_S


class Claim(NamedTuple):
    """One leased job attempt; unpacks into Worker.run_job(*claim)."""

    job_id: int
    kind: str
    payload: dict[str, Any]
    attempt: int
    trace_id: str | None = None
    run_at: datetime | None = None


def int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class Worker:
    def __init__(self, container: Container, handlers: dict[str, Handler] | None = None) -> None:
        self.c = container
        self.handlers = handlers if handlers is not None else HANDLERS
        self.stop = asyncio.Event()
        # One poll task per channel kind: a slow Instagram sweep (2-5 s per call) must not hold
        # back the Telegram userbot, which polls every ~20 s.
        self.poll_tasks: dict[ChannelKind, asyncio.Task[None]] = {}
        self.last_poll: dict[ChannelKind, float] = {}
        self.job_tasks: dict[int, asyncio.Task[None]] = {}
        self.purge_task: asyncio.Task[int] | None = None
        self.last_purge = float("-inf")

    # ---- jobs ----------------------------------------------------------------------------
    async def claim_jobs(self, limit: int) -> list[Claim]:
        async with transaction(self.c.factory) as s:
            jobs = await JobRepo(s).claim(
                self.c.clock(), lease_seconds(self.c.settings), limit,
                exclude_ids=tuple(self.job_tasks),
            )
            # attempts increases on every claim: keep our generation across handler I/O.
            return [Claim(j.id, j.kind, dict(j.payload or {}), j.attempts, j.trace_id, j.run_at)
                    for j in jobs]

    def reap_jobs(self) -> None:
        for job_id, task in list(self.job_tasks.items()):
            if task.done():
                del self.job_tasks[job_id]
                if not task.cancelled():
                    error = task.exception()
                    if error is not None:
                        log.error("job %s task crashed", job_id,
                                  exc_info=(type(error), error, error.__traceback__))

    async def dispatch(self) -> int:
        """Fill free execution slots without waiting for slower jobs in the previous batch."""
        self.reap_jobs()
        capacity = CLAIM_BATCH - len(self.job_tasks)
        if capacity <= 0 or self.stop.is_set():
            return 0
        snapshot = await self.claim_jobs(capacity)
        # A stop received during claim still drains these already-leased jobs.
        for item in snapshot:
            self.job_tasks[item[0]] = asyncio.create_task(self.run_job(*item))
        return len(snapshot)

    async def tick(self) -> int:
        """Claim due jobs and run them concurrently. Returns how many were run."""
        snapshot = await self.claim_jobs(CLAIM_BATCH)
        if snapshot:
            await asyncio.gather(*(self.run_job(*item) for item in snapshot))
        return len(snapshot)

    async def run_job(  # noqa: PLR0913
        self, job_id: int, kind: str, payload: dict, claim_attempt: int,
        trace_id: str | None = None, run_at: datetime | None = None,
    ) -> None:
        """Run one claimed attempt inside the trace that enqueued the job (a new one if none)."""
        tracer = self.c.tracer()
        async with tracer.scope(trace_id, actor="worker", job_id=job_id,
                                dialog_id=int_or_none(payload.get("dialog_id")),
                                message_id=int_or_none(payload.get("message_id"))):
            started_at, start = self.c.clock(), time.perf_counter()
            status, error, detail = await self.attempt(job_id, kind, payload, claim_attempt)
            if run_at is not None:
                detail["queue_ms"] = max(int((started_at - run_at).total_seconds() * 1000), 0)
            await tracer.record(
                tracing.JOB, kind, status=status, started_at=started_at,
                duration_ms=tracing.elapsed_ms(start), attempt=claim_attempt, error=error, detail=detail,
            )

    async def attempt(
        self, job_id: int, kind: str, payload: dict, claim_attempt: int
    ) -> tuple[str, str | None, dict[str, Any]]:
        """Run the handler and settle the claim. Returns the step's (status, error, detail)."""
        handler = self.handlers.get(kind)
        error: str | None = None
        retry = True
        detail: dict[str, Any] = {}
        if handler is None:
            error, retry = f"unknown job kind {kind!r}", False
        else:
            try:
                async with transaction(self.c.factory) as s:
                    detail.update(await handler.run(self.c, s, payload) or {})
            except (ConversationError, SendUncertain) as exc:
                error, retry = str(exc), False
            except Exception as exc:  # any failure is retried with backoff
                log.warning("job %s (%s) failed: %s", job_id, kind, type(exc).__name__)
                error = f"{type(exc).__name__}: {exc}"
        async with transaction(self.c.factory) as s:
            repo = JobRepo(s)
            if error is None:
                done = await repo.complete(job_id, self.c.clock(), claim_attempt=claim_attempt)
                if not done:
                    detail["fenced"] = True  # a newer claim or a terminal state won
                return (tracing.OK if done else tracing.SKIPPED), None, detail
            job = await s.get(Job, job_id)
            if job is None:
                return tracing.ERROR, error, detail
            exhausted = await repo.fail(
                job, error, self.c.clock(), claim_attempt=claim_attempt, retry=retry
            )
            detail["max_attempts"] = job.max_attempts
        if exhausted and handler is not None and handler.giveup is not None:
            detail["gave_up"] = True
            try:
                async with transaction(self.c.factory) as s:
                    await handler.giveup(self.c, s, payload)
            except Exception:
                log.exception("giveup of job %s failed", job_id)
        return (tracing.ERROR if exhausted or not retry else tracing.RETRY), error, detail

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
            channels = [(ch.id, ch.kind) for ch in await ChannelRepo(s).pollable(kinds)]
        total = 0
        tracer = self.c.tracer()
        for channel_id, kind in channels:
            started_at, start = self.c.clock(), time.perf_counter()
            try:
                count = await self.poll_one(channel_id)
            except Exception as exc:
                log.warning("poll of channel %s failed: %s", channel_id, type(exc).__name__)
                await self.record_poll_error(channel_id, f"{type(exc).__name__}: {exc}"[:300])
                await tracer.record(tracing.POLL, f"poll {kind.value}", status=tracing.ERROR,
                                    started_at=started_at, duration_ms=tracing.elapsed_ms(start),
                                    error=tracing.error_text(exc), channel_id=channel_id)
                continue
            total += count
            if count:  # an empty sweep every few seconds would only be noise in the log
                await tracer.record(tracing.POLL, f"poll {kind.value}", started_at=started_at,
                                    duration_ms=tracing.elapsed_ms(start), detail={"messages": count},
                                    channel_id=channel_id)
        return total

    async def poll_one(self, channel_id: int) -> int:
        # Network I/O (seconds) happens with no DB transaction open; ingestion gets its own short one.
        async with self.c.factory() as s:
            channel = await ChannelRepo(s).get(channel_id)
            if channel is None:
                return 0
            kind, session = channel.kind, self.c.box.decrypt_json(channel.session_enc)
        port = cast(PollingChannelPort, self.c.registry[kind].build_port(channel_id, session))
        inbound = await port.fetch_inbound()
        # Every polled message starts its own trace (its reply job continues it); the steps
        # are written once the ingest transaction is over.
        traces: list[TraceContext] = []
        try:
            async with transaction(self.c.factory) as s:
                channel = await ChannelRepo(s).get(channel_id)
                if channel is None:   # deleted while we were fetching
                    return 0
                ingest = self.c.ingest(s)
                for msg in inbound:
                    with trace_scope(actor=kind.value, channel_id=channel_id) as ctx:
                        traces.append(ctx)
                        await ingest.ingest(channel, msg)
                channel.last_error = None
                return len(inbound)
        finally:
            tracer = self.c.tracer()
            for ctx in traces:
                await tracer.flush(ctx)

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

    # ---- system log retention ------------------------------------------------------------
    async def purge_traces(self) -> int:
        try:
            deleted = await self.c.tracer().purge(self.c.settings.trace_retention_days)
        except Exception:
            log.exception("trace log purge failed")
            return 0
        if deleted:
            log.info("trace log purge: %d old steps deleted", deleted)
        return deleted

    def maybe_start_purge(self, now: float) -> None:
        busy = self.purge_task is not None and not self.purge_task.done()
        if not busy and now - self.last_purge >= self.c.settings.trace_purge_interval_s:
            self.last_purge = now
            self.purge_task = asyncio.create_task(self.purge_traces())

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
        stop_task = asyncio.create_task(self.stop.wait())
        try:
            while not self.stop.is_set():
                try:
                    await self.dispatch()
                    self.maybe_start_poll(asyncio.get_running_loop().time())
                    self.maybe_start_purge(asyncio.get_running_loop().time())
                except Exception:
                    log.exception("worker tick failed")
                # A completion immediately frees a slot. The timeout also checks newly due
                # work and polling schedules even when every running job is slow.
                await asyncio.wait(
                    [stop_task, *self.job_tasks.values()],
                    timeout=self.c.settings.worker_tick_s,
                    return_when=asyncio.FIRST_COMPLETED,
                )
        finally:
            stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)
            purge = [self.purge_task] if self.purge_task is not None else []
            await asyncio.gather(*self.job_tasks.values(), *self.poll_tasks.values(), *purge,
                                 return_exceptions=True)
            self.reap_jobs()
        log.info("worker stopped")
