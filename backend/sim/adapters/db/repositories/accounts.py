"""Admin users, their sessions, one-time login hashes and break-glass login links."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import (
    AdminUser,
    AuthSession,
    Dialog,
    LoginLink,
    SimAdminPref,
    SimBranch,
    SimMessage,
    UsedLoginHash,
)
from sim.adapters.db.repositories.base import Repository
from sim.domain.enums import Role


class AdminUserRepo(Repository[AdminUser]):
    model = AdminUser

    async def by_telegram_id(self, telegram_id: int) -> AdminUser | None:
        return await self.session.scalar(
            select(AdminUser).where(AdminUser.telegram_id == telegram_id)
        )

    async def by_tg_username(self, tg_username: str) -> AdminUser | None:
        return await self.session.scalar(
            select(AdminUser).where(AdminUser.tg_username == tg_username.lower())
        )

    async def count_active_superadmins(self) -> int:
        return int(
            await self.session.scalar(
                select(func.count(AdminUser.id)).where(
                    AdminUser.role == Role.SUPERADMIN, AdminUser.is_active.is_(True)
                )
            )
            or 0
        )

    async def remove(self, user: AdminUser) -> None:
        """Delete the admin together with what points at it (does not rely on DB-level FKs)."""
        await self.session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
        await self.session.execute(delete(LoginLink).where(LoginLink.user_id == user.id))
        branches = select(SimBranch.id).where(SimBranch.admin_user_id == user.id)
        await self.session.execute(delete(SimMessage).where(SimMessage.branch_id.in_(branches)))
        await self.session.execute(delete(SimBranch).where(SimBranch.admin_user_id == user.id))
        await self.session.execute(delete(SimAdminPref).where(SimAdminPref.admin_user_id == user.id))
        await self.session.execute(
            update(Dialog).where(Dialog.operator_id == user.id).values(operator_id=None)
        )
        await self.delete(user)


class AuthSessionRepo(Repository[AuthSession]):
    model = AuthSession

    async def by_token_hash(self, token_hash: str) -> AuthSession | None:
        return await self.session.scalar(
            select(AuthSession).where(AuthSession.token_hash == token_hash)
        )

    async def purge_expired(self, now: datetime) -> None:
        await self.session.execute(delete(AuthSession).where(AuthSession.expires_at < now))

    async def revoke_user(self, user_id: int) -> None:
        await self.session.execute(delete(AuthSession).where(AuthSession.user_id == user_id))


class UsedLoginHashRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claim(self, value: str, expires_at: datetime) -> bool:
        """Record the hash; False when it was already used (works across processes)."""
        dialect = self.session.get_bind().dialect.name
        insert = (postgresql if dialect == "postgresql" else sqlite).insert
        stmt = (
            insert(UsedLoginHash)
            .values(hash=value, expires_at=expires_at)
            .on_conflict_do_nothing(index_elements=["hash"])
        )
        result = await self.session.execute(stmt)
        return result.rowcount == 1

    async def purge_expired(self, now: datetime) -> None:
        await self.session.execute(delete(UsedLoginHash).where(UsedLoginHash.expires_at < now))


class LoginLinkRepo(Repository[LoginLink]):
    model = LoginLink

    async def by_token_hash(self, token_hash: str) -> LoginLink | None:
        return await self.session.scalar(
            select(LoginLink).where(LoginLink.token_hash == token_hash)
        )

    async def consume(self, link_id: int) -> bool:
        """Delete the link; False when somebody else consumed it first."""
        result = await self.session.execute(delete(LoginLink).where(LoginLink.id == link_id))
        return result.rowcount == 1

    async def purge_expired(self, now: datetime) -> None:
        await self.session.execute(delete(LoginLink).where(LoginLink.expires_at < now))
