"""Analytics and partner CSV import."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from sim.api.deps import App, Db, authed, owner_only
from sim.clock import to_naive_utc, utcnow

analytics = APIRouter(prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(authed)])
partner = APIRouter(prefix="/api/partner", tags=["partner"], dependencies=[Depends(owner_only)])

MAX_CSV_BYTES = 2 * 1024 * 1024
DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 400


def parse_bound(value: str, *, end: bool) -> datetime:
    """`2026-11-05` (a whole day; as `to` it is inclusive) or a full ISO datetime."""
    try:
        if len(value) <= 10:
            day = date.fromisoformat(value)
            return datetime.combine(day + timedelta(days=1 if end else 0), time.min)
        return to_naive_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError as exc:
        raise HTTPException(422, f"bad date: {value!r}") from exc


@analytics.get("/summary")
async def summary(
    c: App,
    s: Db,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
    persona_id: int | None = None,
):
    end = parse_bound(to, end=True) if to else utcnow() + timedelta(seconds=1)
    start = parse_bound(from_, end=False) if from_ else end - timedelta(days=DEFAULT_RANGE_DAYS)
    if start >= end:
        raise HTTPException(422, "'from' must be before 'to'")
    if end - start > timedelta(days=MAX_RANGE_DAYS):
        raise HTTPException(422, f"range is limited to {MAX_RANGE_DAYS} days")
    return await c.analytics(s).summary(start, end, persona_id)


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
