"""Durable outbound fence: never authorize another send once an attempt was committed."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.adapters.db.models import OutboundSendIntent
from sim.clock import Clock

UNCERTAIN_SEND = "Delivery outcome uncertain; reconcile the channel before resending"


class SendUncertain(Exception):
    """Terminal send failure: retries may reconcile a receipt, but must never send again."""


@dataclass(frozen=True)
class SendReceipt:
    external_id: str | None
    sent_at: datetime


class SendReceipts:
    def __init__(self, factory: async_sessionmaker[AsyncSession], clock: Clock) -> None:
        self.factory, self.clock = factory, clock

    async def previous(self, message_id: int, fingerprint: str) -> SendReceipt | None:
        async with self.factory() as session:
            intent = await session.get(OutboundSendIntent, message_id)
            if intent is None:
                return None
            if intent.fingerprint != fingerprint or intent.sent_at is None:
                raise SendUncertain(UNCERTAIN_SEND)
            return SendReceipt(intent.external_id, intent.sent_at)

    async def claim(self, message_id: int, fingerprint: str) -> None:
        # Must commit before invoking any transport. Concurrent insert losers never send.
        try:
            async with self.factory() as session, session.begin():
                session.add(OutboundSendIntent(
                    message_id=message_id, fingerprint=fingerprint, created_at=self.clock(),
                ))
        except IntegrityError:
            raise SendUncertain(UNCERTAIN_SEND) from None

    async def acknowledge(
        self, message_id: int, fingerprint: str, external_id: str | None,
    ) -> SendReceipt:
        sent_at = self.clock()
        async with self.factory() as session, session.begin():
            intent = await session.get(OutboundSendIntent, message_id)
            if intent is None or intent.fingerprint != fingerprint:
                raise SendUncertain(UNCERTAIN_SEND)
            intent.external_id, intent.sent_at = external_id, sent_at
        return SendReceipt(external_id, sent_at)
