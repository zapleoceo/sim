"""System trace log (trace_event table): one row per step a request goes through.

A trace id is born at the edge (an HTTP request, a polled inbound message, a worker job without
one) and travels in a ContextVar. Jobs enqueued inside a trace store its id, so the worker
continues the same trace: inbound message -> reply job -> LLM calls -> guard -> send job.

Rows are written in their OWN transaction (like the llm_call audit): a failed step stays in the
log even when the caller's unit of work rolls back. A trace write never breaks the caller.
Nothing secret is stored: errors and paths go through `mask()`, details are small and explicit.
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.adapters.db.models import TraceEvent
from sim.adapters.db.repositories.traces import PURGE_BATCH, TraceRepo
from sim.adapters.db.session import transaction
from sim.clock import Clock, utcnow
from sim.trace_context import (  # noqa: F401  (re-exported: one import for callers)
    TraceContext,
    current,
    current_trace_id,
    new_trace_id,
    trace_scope,
)

log = logging.getLogger(__name__)

# Kinds of steps (the log's "Тип" column).
HTTP = "http"          # an admin/API request
WEBHOOK = "webhook"    # a channel or partner callback (/hooks/*)
INBOUND = "inbound"    # one client message accepted (or skipped) by ingest
POLL = "poll"          # a polling channel sweep that brought messages or failed
JOB = "job"            # one worker attempt of a queued job
LLM = "llm"            # one broker call (generator, follow-up, media, content...)
GUARD = "guard"        # one pre-send guard call
SEND = "send"          # one transport send of an outbound message
PARTNER = "partner"    # a partner (registration/deposit) event recorded
WATCH = "watch"        # a reply that was not created or is overdue (sim.services.reply_journal)
KINDS = (HTTP, WEBHOOK, INBOUND, POLL, JOB, LLM, GUARD, SEND, PARTNER, WATCH)

OK = "ok"
ERROR = "error"
RETRY = "retry"        # a job attempt failed and will be retried
SKIPPED = "skipped"    # nothing done on purpose (duplicate, cancelled, fenced out...)
WARNING = "warning"    # nothing failed, but a human should look (a message waits too long)
STATUSES = (OK, ERROR, RETRY, SKIPPED, WARNING)

LINKS = ("admin_id", "client_id", "dialog_id", "channel_id", "job_id", "message_id", "llm_call_id")
ERROR_LIMIT = 1000
NAME_LIMIT = 200

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b"), "***"),                    # Telegram bot token
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer ***"),
    (re.compile(r"(?i)(://)[^/\s:@]+:[^/\s@]+@"), r"\1***@"),                 # user:pass@host
    (re.compile(r"(?i)(?<![a-z])(token|key|secret|password|passwd|signature|cookie)"
                r"(\s*[=:]\s*)[^\s&,;\"']+"), r"\1\2***"),
    (re.compile(r"(?i)(?<![a-z])(hash|sig|auth|code|session)(=)[^\s&,;\"']+"), r"\1\2***"),
    (re.compile(r"\b[A-Za-z0-9_-]{40,}\b"), "***"),                           # long opaque strings
)


def mask(text: str | None, limit: int = ERROR_LIMIT) -> str | None:
    """Redact credentials from free text (errors, paths, query strings) and cut it."""
    if not text:
        return text
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text[:limit]


def error_text(exc: BaseException) -> str:
    return mask(f"{type(exc).__name__}: {exc}") or type(exc).__name__



@dataclass
class Step:
    """A step being timed by `Tracer.step()`; the block may refine its outcome."""

    kind: str
    name: str
    status: str = OK
    error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    links: dict[str, int | None] = field(default_factory=dict)
    attempt: int | None = None
    http_status: int | None = None


class Tracer:
    """Records steps. Inside a scope they are buffered and written after the unit of work;
    outside one (a service called directly, e.g. by a test or a script) they join the caller's
    `session` when given, else get their own transaction."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        clock: Clock = utcnow,
        session: AsyncSession | None = None,
    ) -> None:
        self.factory = factory
        self.clock = clock
        self.session = session

    async def record(  # noqa: PLR0913
        self,
        kind: str,
        name: str,
        *,
        status: str = OK,
        started_at: datetime | None = None,
        duration_ms: int | None = None,
        attempt: int | None = None,
        http_status: int | None = None,
        error: str | None = None,
        detail: dict[str, Any] | None = None,
        trace_id: str | None = None,
        **links: int | None,
    ) -> None:
        ctx = current()
        merged: dict[str, int | None] = dict(ctx.links) if ctx else {}
        merged.update({k: v for k, v in links.items() if v is not None})
        event = TraceEvent(
            trace_id=trace_id or (ctx.trace_id if ctx else new_trace_id()),
            kind=kind,
            name=(mask(name, NAME_LIMIT) or "")[:NAME_LIMIT],
            status=status,
            started_at=started_at or self.clock(),
            duration_ms=duration_ms,
            attempt=attempt,
            http_status=http_status,
            actor=(ctx.actor if ctx else "")[:120],
            error=mask(error),
            detail=detail or None,
            **{k: merged.get(k) for k in LINKS},
        )
        if ctx is not None:
            ctx.pending.append(event)
        elif self.session is not None:
            self.session.add(event)
        else:
            await self.write([event])

    async def write(self, events: list[TraceEvent]) -> None:
        if not events:
            return
        try:
            async with transaction(self.factory) as session:
                session.add_all(events)
        except Exception:  # the log must never break the work it describes
            log.exception("trace_event write failed (%d steps)", len(events))

    async def flush(self, ctx: TraceContext) -> None:
        """Write the steps a scope collected (after its unit of work committed or rolled back)."""
        events, ctx.pending = ctx.pending, []
        for event in events:  # who acted may be known only later (the auth dependency)
            event.actor = event.actor or ctx.actor[:120]
            if event.admin_id is None:
                event.admin_id = ctx.links.get("admin_id")
        await self.write(events)

    @asynccontextmanager
    async def scope(
        self, trace_id: str | None = None, *, actor: str = "", **links: int | None
    ) -> AsyncIterator[TraceContext]:
        """A trace for this block; its steps are written in one transaction when it ends."""
        with trace_scope(trace_id, actor=actor, **links) as ctx:
            try:
                yield ctx
            finally:
                await self.flush(ctx)

    @asynccontextmanager
    async def step(self, kind: str, name: str, **links: int | None) -> AsyncIterator[Step]:
        """Time a block and record it; an exception marks it as an error and propagates."""
        step = Step(kind, name, links=dict(links))
        started_at, start = self.clock(), time.perf_counter()
        try:
            yield step
        except BaseException as exc:
            step.status, step.error = ERROR, step.error or error_text(exc)
            raise
        finally:
            await self.record(
                step.kind, step.name, status=step.status, started_at=started_at,
                duration_ms=elapsed_ms(start), attempt=step.attempt, http_status=step.http_status,
                error=step.error, detail=step.detail, **step.links,
            )

    async def purge(self, retention_days: int, batch: int = PURGE_BATCH) -> int:
        """Retention: delete steps older than `retention_days`, one short transaction per batch.
        0 or less keeps everything."""
        if retention_days <= 0:
            return 0
        cutoff = self.clock() - timedelta(days=retention_days)
        deleted = 0
        while True:
            async with transaction(self.factory) as session:
                count = await TraceRepo(session).purge_batch(cutoff, batch)
            deleted += count
            if count < batch:
                return deleted


def elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)
