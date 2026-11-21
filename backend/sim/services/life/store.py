"""Loading the persona's life around a moment: her world, the planned days from yesterday to
tomorrow, active story arcs and canon. Everything else in sim/services/life is pure and works
on `LifeState`."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Persona, PersonaArc, PersonaCanon, PersonaDay
from sim.domain.enums import ArcStatus, CanonStatus
from sim.domain.life import DayEvent, PersonaLife
from sim.domain.scene import SceneCard
from sim.services.context.facts import to_local
from sim.services.life.agenda import Agenda, build_agenda
from sim.services.life.scene import scene_at
from sim.services.life.world import day_events, life_of

CANON_LIMIT = 40


@dataclass
class LifeState:
    persona: Persona
    life: PersonaLife
    now: datetime
    local: datetime
    days: dict[date, PersonaDay] = field(default_factory=dict)
    arcs: list[PersonaArc] = field(default_factory=list)
    canon: list[PersonaCanon] = field(default_factory=list)

    @property
    def today(self) -> date:
        return self.local.date()

    @property
    def has_life(self) -> bool:
        """Something to build a scene from: a routine or a planned day."""
        return bool(self.life.routine) or any(self.plan(d) for d in self.days)

    def plan(self, day: date) -> list[DayEvent]:
        row = self.days.get(day)
        return day_events(row.events) if row is not None else []

    def agenda(self, day: date | None = None) -> Agenda:
        day = day or self.today
        plans = {d: self.plan(d) for d in (day - timedelta(days=1), day)}
        return build_agenda(day, self.life, plans)

    def scene(self) -> SceneCard:
        return scene_at(self.now, self.agenda(), self.persona)


async def load_days(session: AsyncSession, persona_id: int, first: date,
                    last: date) -> dict[date, PersonaDay]:
    rows = await session.scalars(
        select(PersonaDay).where(PersonaDay.persona_id == persona_id,
                                 PersonaDay.day >= first, PersonaDay.day <= last))
    return {row.day: row for row in rows}


async def load_life(session: AsyncSession, persona: Persona, now: datetime, *,
                    full: bool = False) -> LifeState:
    """Days from the day before yesterday to tomorrow; `full` adds arcs and canon (context)."""
    local = to_local(now, persona.timezone)
    today = local.date()
    state = LifeState(persona=persona, life=life_of(persona), now=now, local=local,
                      days=await load_days(session, persona.id, today - timedelta(days=2),
                                           today + timedelta(days=1)))
    if full:
        state.arcs = list(await session.scalars(
            select(PersonaArc).where(PersonaArc.persona_id == persona.id,
                                     PersonaArc.status == ArcStatus.ACTIVE)
            .order_by(PersonaArc.id)))
        newest = list(await session.scalars(
            select(PersonaCanon).where(PersonaCanon.persona_id == persona.id,
                                       PersonaCanon.status == CanonStatus.ACTIVE)
            .order_by(PersonaCanon.id.desc()).limit(CANON_LIMIT)))
        state.canon = list(reversed(newest))
    return state
