"""Admin users and their sessions."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select

from sim.adapters.db.models import AdminUser, AuthSession
from sim.adapters.db.repositories.base import Repository


class AdminUserRepo(Repository[AdminUser]):
    model = AdminUser

    async def by_username(self, username: str) -> AdminUser | None:
        return await self.session.scalar(select(AdminUser).where(AdminUser.username == username))

    async def count(self) -> int:
        return int(await self.session.scalar(select(func.count(AdminUser.id))) or 0)


class AuthSessionRepo(Repository[AuthSession]):
    model = AuthSession

    async def by_token_hash(self, token_hash: str) -> AuthSession | None:
        return await self.session.scalar(
            select(AuthSession).where(AuthSession.token_hash == token_hash)
        )

    async def purge_expired(self, now: datetime) -> None:
        await self.session.execute(delete(AuthSession).where(AuthSession.expires_at < now))
