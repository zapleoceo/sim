"""scripts/load_life.py: the owner's life JSON goes into `persona.life` and `persona_arc`,
idempotently (the world is replaced, arcs are upserted by title)."""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Persona, PersonaArc
from sim.domain.enums import ArcStatus
from tests.services.life_seed import SEED

BACKEND = Path(__file__).resolve().parents[1]


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


load_life = load("load_life", BACKEND / "scripts" / "load_life.py")
TODAY = date(2026, 11, 21)


def test_the_seed_shape_is_accepted() -> None:
    seed = load_life.LifeSeed.model_validate(SEED)
    assert len(seed.life.routine) == 5 and seed.arcs[0].title == "Ремонт в ванной"
    with pytest.raises(ValidationError):
        load_life.LifeSeed.model_validate({"life": {"routine": [{"days": [1]}]}})


async def test_apply_is_idempotent(db: AsyncSession) -> None:
    persona = Persona(name="Марина")
    db.add(persona)
    await db.flush()
    seed = load_life.LifeSeed.model_validate(SEED)

    first = await load_life.apply_seed(db, persona, seed, TODAY)
    assert (first.routine, first.people, first.arcs_created, first.arcs_updated) == (5, 2, 1, 0)
    changed = seed.model_copy(deep=True)
    changed.arcs[0].stage = "мастер пришёл"
    changed.arcs[0].status = ArcStatus.PAUSED
    second = await load_life.apply_seed(db, persona, changed, TODAY)
    assert (second.arcs_created, second.arcs_updated) == (0, 1)

    [arc] = (await db.scalars(select(PersonaArc))).all()
    assert (arc.stage, arc.status, arc.started_on) == ("мастер пришёл", ArcStatus.PAUSED,
                                                       TODAY)
    assert persona.life["routine"][0]["activity"] == "сон, телефон в другой комнате"
