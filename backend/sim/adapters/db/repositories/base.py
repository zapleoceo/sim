"""Tiny generic repository: the common get/add/delete/list, subclassed per aggregate."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


class Repository[T]:
    model: type[T]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, obj_id: int) -> T | None:
        return await self.session.get(self.model, obj_id)

    async def add(self, obj: T) -> T:
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def delete(self, obj: T) -> None:
        await self.session.delete(obj)
        await self.session.flush()

    async def all(self) -> Sequence[T]:
        res = await self.session.scalars(select(self.model).order_by(self.model.id))  # type: ignore[attr-defined]
        return res.all()

    async def page(
        self, stmt: Select[Any], limit: int, offset: int
    ) -> tuple[Sequence[Any], int]:
        """(rows, total) for a filtered SELECT of entities."""
        total = await self.session.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = (await self.session.scalars(stmt.limit(limit).offset(offset))).unique().all()
        return rows, int(total or 0)
