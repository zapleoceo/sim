"""System trace log reads (filter/sort/page, one trace end to end) and retention purge."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import ColumnElement, Select, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import Client, Dialog, LlmCall, TraceEvent
from sim.adapters.db.repositories.activity import literal_pattern

PURGE_BATCH = 5000

# A step links a client directly or through its dialog (LLM and job steps know only the dialog).
CLIENT_ID = func.coalesce(TraceEvent.client_id, Dialog.client_id)

SortField = Literal["started_at", "kind", "name", "status", "duration_ms", "attempt", "http_status",
                    "actor", "client", "trace_id"]
SORTABLE: dict[SortField, Any] = {
    "started_at": TraceEvent.started_at,
    "kind": TraceEvent.kind,
    "name": TraceEvent.name,
    "status": TraceEvent.status,
    "duration_ms": TraceEvent.duration_ms,
    "attempt": TraceEvent.attempt,
    "http_status": TraceEvent.http_status,
    "actor": TraceEvent.actor,
    "client": Client.display_name,
    "trace_id": TraceEvent.trace_id,
}


@dataclass(frozen=True)
class TraceFilter:
    q: str | None = None
    kinds: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    name: str | None = None
    actor: str | None = None
    client: str | None = None
    trace_id: str | None = None
    client_id: int | None = None
    dialog_id: int | None = None
    channel_id: int | None = None
    job_id: int | None = None
    http_status: int | None = None
    min_ms: int | None = None
    max_ms: int | None = None
    min_attempt: int | None = None
    has_error: bool | None = None
    start: datetime | None = None
    end: datetime | None = None


@dataclass(frozen=True)
class TraceSort:
    field: SortField = "started_at"
    desc: bool = True


def contains(column: Any, value: str) -> ColumnElement[bool]:
    return column.ilike(literal_pattern(value.strip()), escape="\\")


def conditions(f: TraceFilter) -> list[ColumnElement[bool]]:
    where: list[ColumnElement[bool]] = []
    if f.kinds:
        where.append(TraceEvent.kind.in_(f.kinds))
    if f.statuses:
        where.append(TraceEvent.status.in_(f.statuses))
    for column, text in ((TraceEvent.name, f.name), (TraceEvent.actor, f.actor), (Client.display_name, f.client)):
        if text and text.strip():
            where.append(contains(column, text))
    if f.trace_id and f.trace_id.strip():
        where.append(TraceEvent.trace_id.startswith(f.trace_id.strip().lower(), autoescape=True))
    for column, value in ((CLIENT_ID, f.client_id), (TraceEvent.dialog_id, f.dialog_id),
                          (TraceEvent.channel_id, f.channel_id), (TraceEvent.job_id, f.job_id),
                          (TraceEvent.http_status, f.http_status)):
        if value is not None:
            where.append(column == value)
    if f.min_ms is not None:
        where.append(TraceEvent.duration_ms >= f.min_ms)
    if f.max_ms is not None:
        where.append(TraceEvent.duration_ms <= f.max_ms)
    if f.min_attempt is not None:
        where.append(TraceEvent.attempt >= f.min_attempt)
    if f.has_error is not None:
        failed = TraceEvent.error.is_not(None) & (TraceEvent.error != "")
        where.append(failed if f.has_error else ~failed)
    if f.start is not None:
        where.append(TraceEvent.started_at >= f.start)
    if f.end is not None:
        where.append(TraceEvent.started_at < f.end)
    if f.q and f.q.strip():
        where.append(or_(*(contains(c, f.q) for c in (
            TraceEvent.name, TraceEvent.error, TraceEvent.actor, TraceEvent.trace_id, Client.display_name,
        ))))
    return where


def with_client(stmt: Select[Any]) -> Select[Any]:
    return (stmt.outerjoin(Dialog, Dialog.id == TraceEvent.dialog_id)
            .outerjoin(Client, Client.id == CLIENT_ID))


class TraceRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def page(self, f: TraceFilter, sort: TraceSort, limit: int, offset: int):
        """(rows of (TraceEvent, client_id, client_name), total)."""
        stmt = with_client(select(TraceEvent, CLIENT_ID, Client.display_name)).where(*conditions(f))
        total = await self.session.scalar(select(func.count()).select_from(stmt.subquery()))
        column = SORTABLE[sort.field]
        order = [column.desc().nulls_last() if sort.desc else column.asc().nulls_last(),
                 TraceEvent.id.desc() if sort.desc else TraceEvent.id.asc()]
        rows = (await self.session.execute(stmt.order_by(*order).limit(limit).offset(offset))).all()
        return rows, int(total or 0)

    async def trace(self, trace_id: str, limit: int = 1000):
        """Every step of one trace plus the attempts of jobs it touched (a job keeps the trace
        that created it, so a merged reply still shows here), oldest first."""
        jobs = select(TraceEvent.job_id).where(TraceEvent.trace_id == trace_id, TraceEvent.job_id.is_not(None))
        stmt = (with_client(select(TraceEvent, CLIENT_ID, Client.display_name))
                .where(or_(TraceEvent.trace_id == trace_id, TraceEvent.job_id.in_(jobs)))
                .order_by(TraceEvent.started_at, TraceEvent.id).limit(limit))
        return (await self.session.execute(stmt)).all()

    async def message_ids(self, kind: str, name: str, dialog_ids: Sequence[int]) -> set[int]:
        """Messages of these dialogs that already have a step of this kind and name."""
        if not dialog_ids:
            return set()
        rows = await self.session.scalars(
            select(TraceEvent.message_id).where(
                TraceEvent.kind == kind, TraceEvent.name == name,
                TraceEvent.dialog_id.in_(dialog_ids), TraceEvent.message_id.is_not(None),
            )
        )
        return {int(row) for row in rows.all()}

    async def llm_calls(self, ids: Sequence[int]) -> dict[int, LlmCall]:
        if not ids:
            return {}
        return {c.id: c for c in await self.session.scalars(select(LlmCall).where(LlmCall.id.in_(ids)))}

    async def purge_batch(self, cutoff: datetime, batch: int = PURGE_BATCH) -> int:
        """Delete up to `batch` steps older than `cutoff`. Returns how many were deleted."""
        ids = select(TraceEvent.id).where(TraceEvent.started_at < cutoff).limit(batch)
        res = await self.session.execute(
            delete(TraceEvent).where(TraceEvent.id.in_(ids)).execution_options(synchronize_session=False)
        )
        return res.rowcount or 0  # type: ignore[attr-defined]
