"""Canon: durable facts the persona invented about herself in a chat («соседскую собаку зовут
Рекс»). Stored once (normalized text, sim/services/fact_text.py), shown in the context of every
chat; a fact the owner rejected is never added again."""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import PersonaCanon
from sim.domain.enums import CanonStatus
from sim.services.fact_text import normalize_fact


async def add_canon(session: AsyncSession, *, persona_id: int, facts: Iterable[str],
                    chat_id: int | None, event_id: int | None) -> list[PersonaCanon]:
    """The new rows (facts not stored yet in any status)."""
    known = {normalize_fact(fact) for fact in await session.scalars(
        select(PersonaCanon.fact).where(PersonaCanon.persona_id == persona_id))}
    added = []
    for fact in facts:
        clean = " ".join(fact.split())
        key = normalize_fact(clean)
        if not key or key in known:
            continue
        known.add(key)
        row = PersonaCanon(persona_id=persona_id, fact=clean, chat_id=chat_id,
                           event_id=event_id, status=CanonStatus.ACTIVE)
        session.add(row)
        added.append(row)
    if added:
        await session.flush()
    return added
