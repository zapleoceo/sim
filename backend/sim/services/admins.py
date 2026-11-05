"""Admin management (superadmin only): invite, change role, deactivate, delete."""
from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import AdminUser
from sim.adapters.db.repositories.accounts import AdminUserRepo, AuthSessionRepo
from sim.domain.enums import Role
from sim.services.auth import normalize_tg_username


class AdminError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class AdminService:
    def __init__(self, session: AsyncSession) -> None:
        self.users = AdminUserRepo(session)
        self.sessions = AuthSessionRepo(session)

    async def list_all(self) -> Sequence[AdminUser]:
        return await self.users.all()

    async def get_or_404(self, user_id: int) -> AdminUser:
        user = await self.users.get(user_id)
        if user is None:
            raise AdminError(404, "Admin not found")
        return user

    async def create(
        self, *, telegram_id: int | None, tg_username: str | None, role: Role, display_name: str
    ) -> AdminUser:
        try:
            username = normalize_tg_username(tg_username)
        except ValueError as exc:
            raise AdminError(422, str(exc)) from exc
        if telegram_id is None and username is None:
            raise AdminError(422, "telegram_id or tg_username is required")
        if telegram_id is not None and await self.users.by_telegram_id(telegram_id):
            raise AdminError(409, "Admin with this Telegram id already exists")
        if username is not None and await self.users.by_tg_username(username):
            raise AdminError(409, "Admin with this Telegram username already exists")
        name = display_name or (f"@{username}" if username else str(telegram_id))
        return await self.users.add(
            AdminUser(
                telegram_id=telegram_id, tg_username=username, role=role, is_active=True,
                display_name=name,
            )
        )

    async def update(
        self, actor: AdminUser, user_id: int, *, role: Role | None, is_active: bool | None
    ) -> AdminUser:
        user = await self.get_or_404(user_id)
        demotes = role is not None and role is not user.role
        deactivates = is_active is False and user.is_active
        if user.id == actor.id and (demotes or deactivates):
            raise AdminError(409, "You cannot change your own role or deactivate yourself")
        if (demotes and user.role is Role.SUPERADMIN) or deactivates:
            await self.ensure_not_last_superadmin(user)
        if role is not None:
            user.role = role
        if is_active is not None:
            user.is_active = is_active
        if deactivates:
            await self.sessions.revoke_user(user.id)
        await self.users.session.flush()
        return user

    async def delete(self, actor: AdminUser, user_id: int) -> None:
        user = await self.get_or_404(user_id)
        if user.id == actor.id:
            raise AdminError(409, "You cannot delete yourself")
        await self.ensure_not_last_superadmin(user)
        await self.users.remove(user)

    async def ensure_not_last_superadmin(self, user: AdminUser) -> None:
        if (
            user.role is Role.SUPERADMIN
            and user.is_active
            and await self.users.count_active_superadmins() <= 1
        ):
            raise AdminError(409, "The last active superadmin cannot be removed or demoted")
