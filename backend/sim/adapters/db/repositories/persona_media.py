"""Repository for the persona media gallery."""
from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select

from sim.adapters.db.models import PersonaMedia
from sim.adapters.db.repositories.base import Repository
from sim.domain.enums import MediaKind


class PersonaMediaRepo(Repository[PersonaMedia]):
    model = PersonaMedia

    async def for_persona(
        self, persona_id: int, *, kind: MediaKind | None = None
    ) -> Sequence[PersonaMedia]:
        stmt = (
            select(PersonaMedia)
            .where(PersonaMedia.persona_id == persona_id)
            .order_by(PersonaMedia.created_at.desc())
        )
        if kind is not None:
            stmt = stmt.where(PersonaMedia.kind == kind)
        return (await self.session.scalars(stmt)).all()

    async def by_tags(
        self, persona_id: int, tags: list[str], *, kind: MediaKind | None = None
    ) -> Sequence[PersonaMedia]:
        stmt = (
            select(PersonaMedia)
            .where(PersonaMedia.persona_id == persona_id)
            .order_by(PersonaMedia.created_at.desc())
        )
        if kind is not None:
            stmt = stmt.where(PersonaMedia.kind == kind)
        rows = (await self.session.scalars(stmt)).all()
        if not tags:
            return rows
        tag_set = set(tags)
        return [r for r in rows if tag_set & set(r.tags)]
