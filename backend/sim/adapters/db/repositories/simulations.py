"""Admin simulation branches, their messages and the per-admin preferences."""
from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select

from sim.adapters.db.models import SimAdminPref, SimBranch, SimMessage
from sim.adapters.db.repositories.base import Repository


class SimBranchRepo(Repository[SimBranch]):
    model = SimBranch

    async def latest(self, admin_user_id: int) -> SimBranch | None:
        """The branch the admin touched last (open or closed): their "current" simulation."""
        return await self.session.scalar(
            select(SimBranch)
            .where(SimBranch.admin_user_id == admin_user_id)
            .order_by(SimBranch.last_active_at.desc(), SimBranch.id.desc())
            .limit(1)
        )

    async def recent(self, admin_user_id: int, limit: int) -> Sequence[SimBranch]:
        return (
            await self.session.scalars(
                select(SimBranch)
                .where(SimBranch.admin_user_id == admin_user_id)
                .order_by(SimBranch.last_active_at.desc(), SimBranch.id.desc())
                .limit(limit)
            )
        ).unique().all()

    def listing(self, persona_id: int | None = None, admin_user_id: int | None = None):
        stmt = select(SimBranch).order_by(SimBranch.last_active_at.desc(), SimBranch.id.desc())
        if persona_id:
            stmt = stmt.where(SimBranch.persona_id == persona_id)
        if admin_user_id:
            stmt = stmt.where(SimBranch.admin_user_id == admin_user_id)
        return stmt


class SimMessageRepo(Repository[SimMessage]):
    model = SimMessage

    async def by_request(self, branch_id: int, request_id: str) -> SimMessage | None:
        return await self.session.scalar(
            select(SimMessage).where(SimMessage.branch_id == branch_id,
                                     SimMessage.client_request_id == request_id)
        )

    async def recent(self, branch_id: int, limit: int) -> list[SimMessage]:
        """The last `limit` messages of the branch, oldest first."""
        rows = (
            await self.session.scalars(
                select(SimMessage)
                .where(SimMessage.branch_id == branch_id)
                .order_by(SimMessage.id.desc())
                .limit(limit)
            )
        ).all()
        return list(reversed(rows))

    async def all_of(self, branch_id: int) -> Sequence[SimMessage]:
        return (
            await self.session.scalars(
                select(SimMessage).where(SimMessage.branch_id == branch_id).order_by(SimMessage.id)
            )
        ).all()

    async def counts(self, branch_ids: Sequence[int]) -> dict[int, int]:
        if not branch_ids:
            return {}
        rows = await self.session.execute(
            select(SimMessage.branch_id, func.count(SimMessage.id))
            .where(SimMessage.branch_id.in_(list(branch_ids)))
            .group_by(SimMessage.branch_id)
        )
        return {branch_id: int(n) for branch_id, n in rows.all()}


class SimPrefRepo(Repository[SimAdminPref]):
    model = SimAdminPref

    async def debug(self, admin_user_id: int) -> bool:
        pref = await self.session.get(SimAdminPref, admin_user_id)
        return True if pref is None else pref.debug

    async def set_debug(self, admin_user_id: int, value: bool) -> None:
        pref = await self.session.get(SimAdminPref, admin_user_id)
        if pref is None:
            await self.add(SimAdminPref(admin_user_id=admin_user_id, debug=value))
        else:
            pref.debug = value
