#!/usr/bin/python3
"""Load the persona's life from a JSON file (docs/persona-life.md, «Загрузка»):

    {"life": {"routine": [...], "people": [...], "hobbies": [...], "places": [...]},
     "arcs": [{"title", "summary", "stage", "status", "started_on"?, "ends_on"?}, ...]}

Usage (in the api container, docs/deploy.md):
    python scripts/load_life.py <life.json> [--persona-id 1]

Settings come from the environment (SIM_DATABASE_URL). Idempotent: `persona.life` is replaced
by the file, arcs are upserted by title (summary, stage, status and dates updated); arcs that
are not in the file are kept. The JSON is validated by the same models as the API."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import SystemClock
from sim.config import get_settings
from sim.db.models import Persona, PersonaArc
from sim.db.session import make_engine, make_sessionmaker, transaction
from sim.domain.enums import ArcStatus
from sim.domain.life import PersonaLife
from sim.services.context.facts import to_local


class ArcSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    stage: str = Field(default="", max_length=4000)
    status: ArcStatus = ArcStatus.ACTIVE
    started_on: date | None = None
    ends_on: date | None = None


class LifeSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    life: PersonaLife = Field(default_factory=PersonaLife)
    arcs: list[ArcSeed] = Field(default_factory=list, max_length=50)


@dataclass
class Report:
    routine: int = 0
    people: int = 0
    arcs_created: int = 0
    arcs_updated: int = 0


async def apply_seed(session: AsyncSession, persona: Persona, seed: LifeSeed,
                     today: date) -> Report:
    """Replace the world, upsert arcs by title. `today` is her local date for new arcs."""
    persona.life = seed.life.model_dump(mode="json")
    report = Report(routine=len(seed.life.routine), people=len(seed.life.people))
    existing = {arc.title.strip().lower(): arc for arc in await session.scalars(
        select(PersonaArc).where(PersonaArc.persona_id == persona.id))}
    for item in seed.arcs:
        arc = existing.get(item.title.strip().lower())
        if arc is None:
            arc = PersonaArc(persona_id=persona.id, title=item.title.strip(),
                             started_on=item.started_on or today)
            session.add(arc)
            report.arcs_created += 1
        else:
            report.arcs_updated += 1
            if item.started_on is not None:
                arc.started_on = item.started_on
        arc.summary, arc.stage = item.summary.strip(), item.stage.strip()
        arc.status, arc.ends_on = item.status, item.ends_on
    await session.flush()
    return report


async def run(document: dict[str, Any], persona_id: int) -> Report:
    seed = LifeSeed.model_validate(document)
    settings = get_settings()
    engine = make_engine(settings.database_url, pool_size=1)
    try:
        async with transaction(make_sessionmaker(engine)) as session:
            persona = await session.get(Persona, persona_id)
            if persona is None:
                raise SystemExit(f"persona {persona_id} not found")
            today = to_local(SystemClock().now(), persona.timezone).date()
            return await apply_seed(session, persona, seed, today)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the persona's life from a JSON file.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--persona-id", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.path.read_text(encoding="utf-8"))
        report = asyncio.run(run(document, args.persona_id))
    except ValidationError as exc:
        print(f"invalid life JSON:\n{exc}", file=sys.stderr)
        return 1
    print(f"life loaded: {report.routine} routine slots, {report.people} people, "
          f"arcs created {report.arcs_created}, updated {report.arcs_updated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
