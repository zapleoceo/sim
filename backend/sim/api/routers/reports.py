"""Analytics and partner CSV import."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from sim.api.date_bounds import parse_bound
from sim.api.deps import App, Db, authed, owner_only
from sim.clock import utcnow
from sim.ports.llm import LLMError

analytics = APIRouter(prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(authed)])
partner = APIRouter(prefix="/api/partner", tags=["partner"], dependencies=[Depends(owner_only)])

MAX_CSV_BYTES = 2 * 1024 * 1024
DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 400


def period(from_: str | None, to: str | None) -> tuple[datetime, datetime]:
    """[start, end) of an analytics request; the default is the last 30 days."""
    end = parse_bound(to, end=True) if to else utcnow() + timedelta(seconds=1)
    start = parse_bound(from_, end=False) if from_ else end - timedelta(days=DEFAULT_RANGE_DAYS)
    if start >= end:
        raise HTTPException(422, "'from' must be before 'to'")
    if end - start > timedelta(days=MAX_RANGE_DAYS):
        raise HTTPException(422, f"range is limited to {MAX_RANGE_DAYS} days")
    return start, end


def day_span(start: datetime, end: datetime) -> tuple[date, date]:
    """Whole UTC days covering [start, end): Broker counts spend by day, `end` exclusive."""
    last = end.date() if end.time() == time.min else end.date() + timedelta(days=1)
    return start.date(), last


@analytics.get("/summary")
async def summary(
    c: App,
    s: Db,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
    persona_id: int | None = None,
):
    start, end = period(from_, to)
    return await c.analytics(s).summary(start, end, persona_id)


@analytics.get("/spend")
async def spend(
    c: App,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
):
    """Money spent by the whole SIM project, as Broker books it (not re-counted here): totals,
    by capability and by day, the project caps and today's / this month's spend. Broker being
    unavailable is not an error of this page: `available: false` with the reason."""
    first, last = day_span(*period(from_, to))
    try:
        usage = await c.llm.usage(first, last)
    except LLMError as exc:
        return {"available": False, "error": str(exc)[:300]}
    return {"available": True, **usage}


@partner.post("/import")
async def import_partner_csv(c: App, s: Db, file: Annotated[UploadFile, File()]):
    content = await file.read(MAX_CSV_BYTES + 1)
    if len(content) > MAX_CSV_BYTES:
        raise HTTPException(413, "CSV is too large")
    try:
        result = await c.partner(s).import_csv(content)
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "CSV must be UTF-8") from exc
    return {"imported": result.imported, "skipped": result.skipped, "errors": result.errors}
