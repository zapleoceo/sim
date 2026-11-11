"""Authenticated message history, owner-only LLM attempt audit and the system trace log."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from sim.adapters.db.models import Client
from sim.adapters.db.repositories.activity import ActivityFilter, ActivityRepo
from sim.adapters.db.repositories.traces import SortField, TraceFilter, TraceRepo, TraceSort
from sim.api.activity_schemas import (
    llm_activity_out,
    message_activity_out,
    trace_out,
    trace_step_out,
)
from sim.api.date_bounds import optional_bounds
from sim.api.deps import App, Db, authed, owner_only
from sim.domain.enums import Author, ChannelKind, Delivery, Direction
from sim.services import tracing
from sim.services.app_settings import trace_retention_days

router = APIRouter(tags=["activity"], dependencies=[Depends(authed)])


class CommonQuery(BaseModel):
    persona_id: int | None = Field(default=None, gt=0)
    channel_id: int | None = Field(default=None, gt=0)
    channel_kind: ChannelKind | None = None
    q: str | None = Field(default=None, max_length=200)
    from_: str | None = Field(default=None, alias="from", max_length=40)
    to: str | None = Field(default=None, max_length=40)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)

    def filters(self, **extra) -> ActivityFilter:
        start, end = optional_bounds(self.from_, self.to)
        return ActivityFilter(
            persona_id=self.persona_id, channel_id=self.channel_id, channel_kind=self.channel_kind,
            q=self.q, start=start, end=end, **extra,
        )


class ClientMessageQuery(CommonQuery):
    direction: Direction | None = None
    author: Author | None = None
    delivery: Delivery | None = None

    def message_filters(self, client_id: int | None) -> ActivityFilter:
        return self.filters(client_id=client_id, direction=self.direction, author=self.author, delivery=self.delivery)


class MessageQuery(ClientMessageQuery):
    client_id: int | None = Field(default=None, gt=0)


class LlmQuery(CommonQuery):
    client_id: int | None = Field(default=None, gt=0)
    workflow: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=120)
    provider: str | None = Field(default=None, max_length=64)
    has_error: bool | None = None


async def message_page(s: Db, query: ClientMessageQuery, client_id: int | None):
    rows, total = await ActivityRepo(s).messages(query.message_filters(client_id), query.limit, query.offset)
    return {"items": [message_activity_out(*row) for row in rows], "total": total,
            "limit": query.limit, "offset": query.offset}


@router.get("/api/activity/messages")
async def messages(s: Db, query: Annotated[MessageQuery, Query()]):
    return await message_page(s, query, query.client_id)


@router.get("/api/clients/{client_id}/messages")
async def client_messages(client_id: int, s: Db, query: Annotated[ClientMessageQuery, Query()]):
    if await s.get(Client, client_id) is None:
        raise HTTPException(404, "Client not found")
    return await message_page(s, query, client_id)


@router.get("/api/activity/llm", dependencies=[Depends(owner_only)])
async def llm(s: Db, query: Annotated[LlmQuery, Query()]):
    filters = query.filters(client_id=query.client_id, workflow=query.workflow,
                            model=query.model, provider=query.provider, has_error=query.has_error)
    rows, total = await ActivityRepo(s).llm(filters, query.limit, query.offset)
    return {"items": [llm_activity_out(*row) for row in rows], "total": total,
            "limit": query.limit, "offset": query.offset}


# ---- system log (owner only) -------------------------------------------------------------
def csv_values(raw: str | None, allowed: tuple[str, ...], field: str) -> tuple[str, ...]:
    values = tuple(v for v in (x.strip() for x in (raw or "").split(",")) if v)
    if unknown := [v for v in values if v not in allowed]:
        raise HTTPException(422, f"Unknown {field}: {', '.join(unknown)}")
    return values


class LogQuery(BaseModel):
    """Every column filters server-side; `sort` takes any column, `q` searches name, error,
    actor, trace id and client name at once."""

    q: str | None = Field(default=None, max_length=200)
    kind: str | None = Field(default=None, max_length=200, description="comma list of kinds")
    status: str | None = Field(default=None, max_length=100, description="comma list of statuses")
    name: str | None = Field(default=None, max_length=200)
    actor: str | None = Field(default=None, max_length=120)
    client: str | None = Field(default=None, max_length=200)
    trace_id: str | None = Field(default=None, max_length=32)
    client_id: int | None = Field(default=None, gt=0)
    dialog_id: int | None = Field(default=None, gt=0)
    channel_id: int | None = Field(default=None, gt=0)
    job_id: int | None = Field(default=None, gt=0)
    http_status: int | None = Field(default=None, ge=100, le=599)
    min_ms: int | None = Field(default=None, ge=0)
    max_ms: int | None = Field(default=None, ge=0)
    min_attempt: int | None = Field(default=None, ge=1)
    has_error: bool | None = None
    from_: str | None = Field(default=None, alias="from", max_length=40)
    to: str | None = Field(default=None, max_length=40)
    sort: SortField = "started_at"
    order: Literal["asc", "desc"] = "desc"
    page: int = Field(default=1, ge=1, le=100_000)
    size: int = Field(default=50, ge=1, le=200)

    def trace_filter(self) -> TraceFilter:
        start, end = optional_bounds(self.from_, self.to)
        if self.min_ms is not None and self.max_ms is not None and self.min_ms > self.max_ms:
            raise HTTPException(422, "'min_ms' must not exceed 'max_ms'")
        return TraceFilter(
            q=self.q, kinds=csv_values(self.kind, tracing.KINDS, "kind"),
            statuses=csv_values(self.status, tracing.STATUSES, "status"),
            name=self.name, actor=self.actor, client=self.client, trace_id=self.trace_id,
            client_id=self.client_id, dialog_id=self.dialog_id, channel_id=self.channel_id,
            job_id=self.job_id, http_status=self.http_status, min_ms=self.min_ms, max_ms=self.max_ms,
            min_attempt=self.min_attempt, has_error=self.has_error, start=start, end=end,
        )


@router.get("/api/activity/log", dependencies=[Depends(owner_only)])
async def system_log(s: Db, c: App, query: Annotated[LogQuery, Query()]):
    rows, total = await TraceRepo(s).page(
        query.trace_filter(), TraceSort(query.sort, query.order == "desc"),
        query.size, (query.page - 1) * query.size,
    )
    return {"items": [trace_step_out(*row) for row in rows], "total": total, "page": query.page,
            "size": query.size, "retention_days": await trace_retention_days(s, c.settings),
            "kinds": list(tracing.KINDS), "statuses": list(tracing.STATUSES)}


@router.get("/api/activity/traces/{trace_id}", dependencies=[Depends(owner_only)])
async def trace(trace_id: Annotated[str, Path(pattern="^[0-9a-f]{32}$")], s: Db):
    repo = TraceRepo(s)
    rows = await repo.trace(trace_id)
    if not rows:
        raise HTTPException(404, "Trace not found")
    calls = await repo.llm_calls([row[0].llm_call_id for row in rows if row[0].llm_call_id])
    return trace_out(trace_id, rows, calls)
