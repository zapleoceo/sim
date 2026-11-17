"""Encrypted private Studio drafts. No provider, operation journal or queue access."""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from sim.adapters.db.base import Base
from sim.adapters.db.models import AdminUser
from sim.adapters.db.session import transaction


class StudioDraft(Base):
    __tablename__ = "studio_draft"

    owner_id: Mapped[int] = mapped_column(ForeignKey("admin_user.id"), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    text_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)


class DraftConflict(Exception):
    pass


class StudioDrafts:
    def __init__(self, factory, box):
        self.factory, self.box = factory, box

    def output(self, row):
        return {"revision": row.revision if row else 0,
                "text": self.box.decrypt(row.text_encrypted) if row and row.text_encrypted else ""}

    async def read(self, owner_id: int, scope: str):
        async with self.factory() as session:
            return self.output(await session.get(StudioDraft, (owner_id, scope)))

    async def save(self, owner_id: int, scope: str, revision: int, text: str):
        async with transaction(self.factory) as session:
            # Serialize first insert as well as updates; tombstones fence stale saves after delete.
            await session.scalar(select(AdminUser.id).where(
                AdminUser.id == owner_id).with_for_update())
            row = await session.get(StudioDraft, (owner_id, scope))
            if revision != (row.revision if row else 0):
                raise DraftConflict()
            if row is None:
                row = StudioDraft(owner_id=owner_id, scope=scope, revision=0)
                session.add(row)
            row.revision += 1
            row.text_encrypted = self.box.encrypt(text) if text else None
            return self.output(row)
