"""Funnel service: applies domain.funnel rules and writes the funnel_event journal.

Every change also becomes a `stage` / `status` line of the communication journal in each of the
client's dialogs (sim.services.journal), with who made it and why."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import Client, FunnelEvent
from sim.adapters.db.repositories.conversations import FunnelEventRepo
from sim.clock import Clock, utcnow
from sim.domain.enums import ClientStatus, Stage
from sim.domain.funnel import Actor, FunnelRuleError, next_stage
from sim.services.journal import SOME_OPERATOR, SYSTEM, THE_MODEL, CommunicationJournal, Who

__all__ = ["MODEL_DECISION", "FunnelRuleError", "FunnelService"]

# The funnel_event reason of a stage the model chose itself. It names the actor, not a cause:
# the communication journal leaves the reason of such a record empty.
MODEL_DECISION = "model decision"
ACTORS: dict[Actor, Who] = {
    Actor.AI: THE_MODEL,
    Actor.OPERATOR: SOME_OPERATOR,
    Actor.PARTNER: Who(SYSTEM.actor, "данные партнёра"),
    Actor.SYSTEM: SYSTEM,
}


class FunnelService:
    def __init__(self, session: AsyncSession, *, clock: Clock = utcnow) -> None:
        self.events = FunnelEventRepo(session)
        self.journal = CommunicationJournal(session, clock=clock)

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
        self, client: Client, status: ClientStatus, actor: Actor, reason: str = "",
        who: Who | None = None,
    ) -> bool:
        if client.status == status:
            return False
        await self.record(client, "status", client.status.value, status.value, actor, reason, who)
        client.status = status
        return True

    async def operator_update(
        self,
        client: Client,
        *,
        stage: Stage | None,
        status: ClientStatus | None,
        reason: str = "",
        who: Who | None = None,
    ) -> None:
        """Manual change by an operator (`who`: which one). registered/paid are rejected
        (FunnelRuleError)."""
        if stage is not None:
            await self.advance_any(client, stage, reason, who)
        if status is not None:
            await self.set_status(client, status, Actor.OPERATOR, reason, who)
            if status is ClientStatus.ACTIVE:
                client.no_followups = False

    async def advance_any(
        self, client: Client, stage: Stage, reason: str, who: Who | None = None
    ) -> None:
        new = next_stage(client.stage, stage, Actor.OPERATOR)  # may move backwards
        if new != client.stage:
            await self.record(
                client, "stage", client.stage.value, new.value, Actor.OPERATOR, reason, who)
            client.stage = new

    async def record(  # noqa: PLR0913
        self, client: Client, kind: str, old: str, new: str, actor: Actor, reason: str,
        who: Who | None = None,
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
        await self.journal.record_for_client(
            client.id, kind, old=old, new=new, who=who or ACTORS[actor],
            reason="" if reason == MODEL_DECISION else reason,
        )
