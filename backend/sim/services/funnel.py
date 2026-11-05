"""Funnel service: applies domain.funnel rules and writes the funnel_event journal."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import Client, FunnelEvent
from sim.adapters.db.repositories.conversations import FunnelEventRepo
from sim.domain.enums import ClientStatus, Stage
from sim.domain.funnel import Actor, FunnelRuleError, next_stage

__all__ = ["FunnelRuleError", "FunnelService"]


class FunnelService:
    def __init__(self, session: AsyncSession) -> None:
        self.events = FunnelEventRepo(session)

    async def advance(
        self, client: Client, proposed: Stage, actor: Actor, reason: str = ""
    ) -> bool:
        """Move the client's stage if the rules allow. Raises FunnelRuleError on a forbidden
        move; returns False for a no-op."""
        new = next_stage(client.stage, proposed, actor)
        if new == client.stage:
            return False
        await self.record(client, "stage", client.stage.value, new.value, actor, reason)
        client.stage = new
        return True

    async def set_status(
        self, client: Client, status: ClientStatus, actor: Actor, reason: str = ""
    ) -> bool:
        if client.status == status:
            return False
        await self.record(client, "status", client.status.value, status.value, actor, reason)
        client.status = status
        return True

    async def operator_update(
        self,
        client: Client,
        *,
        stage: Stage | None,
        status: ClientStatus | None,
        reason: str = "",
    ) -> None:
        """Manual change by an operator. registered/paid are rejected (FunnelRuleError)."""
        if stage is not None:
            await self.advance_any(client, stage, reason)
        if status is not None:
            await self.set_status(client, status, Actor.OPERATOR, reason)
            if status is ClientStatus.ACTIVE:
                client.no_followups = False

    async def advance_any(self, client: Client, stage: Stage, reason: str) -> None:
        new = next_stage(client.stage, stage, Actor.OPERATOR)  # may move backwards
        if new != client.stage:
            await self.record(client, "stage", client.stage.value, new.value, Actor.OPERATOR, reason)
            client.stage = new

    async def record(
        self, client: Client, kind: str, old: str, new: str, actor: Actor, reason: str
    ) -> None:
        await self.events.add(
            FunnelEvent(
                client_id=client.id,
                kind=kind,
                from_value=old,
                to_value=new,
                actor=actor.value,
                reason=reason[:1000],
            )
        )
