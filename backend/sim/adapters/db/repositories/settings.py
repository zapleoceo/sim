"""Owner-edited settings: one row per overridden key."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import AppSetting
from sim.clock import utcnow


class AppSettingRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def all(self) -> Sequence[AppSetting]:
        return (await self.session.scalars(select(AppSetting).order_by(AppSetting.key))).all()

    async def get(self, key: str) -> AppSetting | None:
        return await self.session.get(AppSetting, key)

    async def put(self, key: str, value: Any, admin_id: int | None) -> AppSetting:
        row = await self.get(key)
        if row is None:
            row = AppSetting(key=key)
            self.session.add(row)
        row.value, row.updated_by_id, row.updated_at = value, admin_id, utcnow()
        await self.session.flush()
        return row

    async def delete(self, key: str) -> None:
        row = await self.get(key)
        if row is not None:
            await self.session.delete(row)
            await self.session.flush()
