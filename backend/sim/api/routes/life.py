"""`/api/personas/{id}/life`, `/arcs`, `/days`, `/canon`: a persona's life on the dashboard
(docs/persona-life.md, docs/api.md). The world and a day's events are validated by the same
pydantic models the planner and the context use (sim/domain/life.py)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any

from litestar import delete, get, patch, post, put
from litestar.exceptions import ClientException, NotFoundException
from litestar.params import Parameter
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api import views
from sim.api.context import ApiContext
from sim.api.queries import persona_or_404
from sim.api.schemas import ArcIn, ArcPatchIn, CanonPatchIn, sent_fields
from sim.db.models import Persona, PersonaArc, PersonaCanon, PersonaDay
from sim.domain.agents import AgentKey, agent_config
from sim.domain.enums import ArcStatus, CanonStatus, DaySource
from sim.domain.life import DAY_MAX_EVENTS, DayEvent, PersonaLife, day_errors
from sim.services.context.facts import to_local
from sim.services.life.world import life_of

Json = dict[str, Any]
MAX_DAYS_RANGE = 62
EVENTS = TypeAdapter(list[DayEvent])


def pydantic_errors(exc: ValidationError) -> str:
    reasons = [f"{'.'.join(map(str, e['loc'])) or 'тело'}: {e['msg']}" for e in exc.errors()]
    return "Неверные данные: " + "; ".join(reasons[:5])


def local_today(persona: Persona, ctx: ApiContext) -> date:
    return to_local(ctx.deps.clock.now(), persona.timezone).date()


# --- the world -------------------------------------------------------------------------------


@get("/personas/{persona_id:int}/life")
async def get_life(persona_id: int, db: AsyncSession) -> Json:
    return life_of(await persona_or_404(db, persona_id)).model_dump(mode="json")


@put("/personas/{persona_id:int}/life")
async def update_life(persona_id: int, data: dict[str, Any], db: AsyncSession) -> Json:
    persona = await persona_or_404(db, persona_id)
    try:
        life = PersonaLife.model_validate(data)
    except ValidationError as exc:
        raise ClientException(detail=pydantic_errors(exc)) from exc
    persona.life = life.model_dump(mode="json")
    await db.flush()
    return life.model_dump(mode="json")


# --- story arcs ------------------------------------------------------------------------------


async def own_arc(db: AsyncSession, persona: Persona, arc_id: int) -> PersonaArc:
    arc = await db.get(PersonaArc, arc_id)
    if arc is None or arc.persona_id != persona.id:
        raise NotFoundException(detail=f"История не найдена: {arc_id}")
    return arc


@get("/personas/{persona_id:int}/arcs")
async def list_arcs(persona_id: int, db: AsyncSession) -> Json:
    persona = await persona_or_404(db, persona_id)
    arcs = await db.scalars(select(PersonaArc).where(PersonaArc.persona_id == persona.id)
                            .order_by(PersonaArc.id))
    return {"items": [views.arc_view(arc) for arc in arcs]}


@post("/personas/{persona_id:int}/arcs", status_code=201)
async def create_arc(persona_id: int, data: ArcIn, ctx: ApiContext, db: AsyncSession) -> Json:
    persona = await persona_or_404(db, persona_id)
    arc = PersonaArc(persona_id=persona.id, title=data.title.strip(),
                     summary=data.summary.strip(), stage=data.stage.strip(),
                     status=ArcStatus(data.status),
                     started_on=data.started_on or local_today(persona, ctx),
                     ends_on=data.ends_on)
    db.add(arc)
    await db.flush()
    return views.arc_view(arc)


@patch("/personas/{persona_id:int}/arcs/{arc_id:int}")
async def update_arc(persona_id: int, arc_id: int, data: ArcPatchIn, db: AsyncSession) -> Json:
    arc = await own_arc(db, await persona_or_404(db, persona_id), arc_id)
    for field, value in sent_fields(data).items():
        if field == "status":
            value = ArcStatus(value)
        elif isinstance(value, str):
            value = value.strip()
        setattr(arc, field, value)
    await db.flush()
    return views.arc_view(arc)


@delete("/personas/{persona_id:int}/arcs/{arc_id:int}", status_code=204)
async def delete_arc(persona_id: int, arc_id: int, db: AsyncSession) -> None:
    arc = await own_arc(db, await persona_or_404(db, persona_id), arc_id)
    await db.delete(arc)
    await db.flush()


# --- planned days ----------------------------------------------------------------------------


@get("/personas/{persona_id:int}/days")
async def list_days(persona_id: int, ctx: ApiContext, db: AsyncSession,
                    first: Annotated[date | None, Parameter(query="from")] = None,
                    to: date | None = None) -> Json:
    """Days from `from` to `to` (default: yesterday … tomorrow of her local calendar), plus
    her local today and minute of the day, so the dashboard marks what she does now."""
    persona = await persona_or_404(db, persona_id)
    local = to_local(ctx.deps.clock.now(), persona.timezone)
    today = local.date()
    first = first or today - timedelta(days=1)
    to = to or today + timedelta(days=1)
    if to < first or (to - first).days > MAX_DAYS_RANGE:
        raise ClientException(detail=f"Диапазон дат: не больше {MAX_DAYS_RANGE} дней")
    rows = await db.scalars(select(PersonaDay).where(
        PersonaDay.persona_id == persona.id, PersonaDay.day >= first, PersonaDay.day <= to)
        .order_by(PersonaDay.day))
    return {"today": today, "now_minute": local.hour * 60 + local.minute,
            "items": [views.day_view(row) for row in rows]}


@put("/personas/{persona_id:int}/days/{day:date}")
async def update_day(persona_id: int, day: date, data: dict[str, Any],
                     db: AsyncSession) -> Json:
    """The owner's edit: the day is marked `edited` and never re-planned by itself."""
    persona = await persona_or_404(db, persona_id)
    raw = data.get("events")
    if not isinstance(raw, list) or len(raw) > DAY_MAX_EVENTS:
        raise ClientException(detail=f"events: список до {DAY_MAX_EVENTS} событий")
    try:
        events = EVENTS.validate_python(raw)
    except ValidationError as exc:
        raise ClientException(detail=pydantic_errors(exc)) from exc
    if errors := day_errors(events):
        raise ClientException(detail="; ".join(errors))
    summary = data.get("summary")
    row = await db.scalar(select(PersonaDay).where(PersonaDay.persona_id == persona.id,
                                                   PersonaDay.day == day))
    if row is None:
        row = PersonaDay(persona_id=persona.id, day=day, summary="", model="")
        db.add(row)
    row.events = [event.model_dump(mode="json") for event in events]
    if isinstance(summary, str):
        row.summary = summary.strip()[:2000]
    row.source = DaySource.EDITED
    row.error = None
    await db.flush()
    return views.day_view(row)


@post("/personas/{persona_id:int}/days/{day:date}/plan", status_code=202)
async def plan_day(persona_id: int, day: date, ctx: ApiContext, db: AsyncSession) -> Json:
    """Re-plan this day with the daily planner (a task; the page re-reads the day)."""
    persona = await persona_or_404(db, persona_id)
    if not agent_config(persona, AgentKey.LIFE).model:
        raise ClientException(detail="Планировщик выключен: выберите ему модель в «Агентах»")
    await ctx.deps.tasks.plan_day(persona.id, day.isoformat(), True)
    return {"queued": True, "day": day}


# --- canon -----------------------------------------------------------------------------------


@get("/personas/{persona_id:int}/canon")
async def list_canon(persona_id: int, db: AsyncSession, status: str | None = None) -> Json:
    persona = await persona_or_404(db, persona_id)
    query = select(PersonaCanon).where(PersonaCanon.persona_id == persona.id)
    if status in {s.value for s in CanonStatus}:
        query = query.where(PersonaCanon.status == CanonStatus(status))
    items = await db.scalars(query.order_by(PersonaCanon.id.desc()))
    return {"items": [views.canon_view(item) for item in items]}


@patch("/personas/{persona_id:int}/canon/{canon_id:int}")
async def update_canon(persona_id: int, canon_id: int, data: CanonPatchIn,
                       db: AsyncSession) -> Json:
    persona = await persona_or_404(db, persona_id)
    item = await db.get(PersonaCanon, canon_id)
    if item is None or item.persona_id != persona.id:
        raise NotFoundException(detail=f"Факт не найден: {canon_id}")
    sent = sent_fields(data)
    if "fact" in sent:
        item.fact = " ".join(sent["fact"].split())
    if "status" in sent:
        item.status = CanonStatus(sent["status"])
    await db.flush()
    return views.canon_view(item)


ROUTES = [get_life, update_life, list_arcs, create_arc, update_arc, delete_arc, list_days,
          update_day, plan_day, list_canon, update_canon]
