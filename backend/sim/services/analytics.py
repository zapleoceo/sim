"""Analytics summary (docs/api.md). Aggregation is done in Python over a few narrow queries:
MVP volumes, and the same code runs on Postgres and SQLite.

Definitions, for the half-open period [start, end):
- contacts       distinct clients who wrote to us in the period;
- new_contacts   clients created in the period;
- replied        contacts with an outgoing message followed by an incoming one in the period
                 (a real exchange, not a one-sided message);
- by_stage       current stage of the contacts;
- registrations  distinct clients with a partner registration event in the period;
- payments       partner payment events in the period; revenue = sum of their amounts;
- partner_payouts sum of `payout` over all partner events in the period;
- conversion     registrations/contacts and distinct payers/registrations (0 when no base)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import Channel, Client, Dialog, LlmCall, Message, PartnerEvent
from sim.domain.enums import ChannelKind, Direction, PartnerEventKind, Stage


def ratio(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def days_between(start: datetime, end: datetime) -> list[date]:
    last = (end - timedelta(microseconds=1)).date()
    days, cur = [], start.date()
    while cur <= last:
        days.append(cur)
        cur += timedelta(days=1)
    return days


class AnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def summary(
        self, start: datetime, end: datetime, persona_id: int | None = None
    ) -> dict[str, Any]:
        msgs = await self.message_rows(start, end, persona_id)
        contacts: set[int] = set()
        kind_by_client: dict[int, ChannelKind] = {}
        thread: dict[int, list[tuple[datetime, Direction]]] = defaultdict(list)
        for client_id, kind, direction, at in msgs:
            thread[client_id].append((at, direction))
            kind_by_client.setdefault(client_id, kind)
            if direction is Direction.IN:
                contacts.add(client_id)
        replied = sum(1 for cid in contacts if self.has_exchange(thread[cid]))

        new_clients = await self.new_clients(start, end, persona_id)
        by_stage = {s.value: 0 for s in Stage}
        if contacts:
            stages = await self.session.scalars(select(Client.stage).where(Client.id.in_(contacts)))
            for stage in stages:
                by_stage[stage.value] += 1

        events = await self.event_rows(start, end, persona_id)
        reg_clients = {e.client_id for e in events if e.kind is PartnerEventKind.REGISTRATION}
        pay_events = [e for e in events if e.kind is PartnerEventKind.PAYMENT]
        pay_clients = {e.client_id for e in pay_events}
        channel_of = await self.first_channel_kinds({e.client_id for e in events})

        by_channel = []
        for kind in ChannelKind:
            ctc = {c for c in contacts if kind_by_client.get(c) is kind}
            by_channel.append(
                {
                    "kind": kind.value,
                    "contacts": len(ctc),
                    "registrations": sum(1 for c in reg_clients if channel_of.get(c) is kind),
                    "payments": sum(1 for e in pay_events if channel_of.get(e.client_id) is kind),
                }
            )

        daily = {d: {"contacts": 0, "registrations": 0, "payments": 0} for d in days_between(start, end)}
        for created in new_clients:
            daily[created.date()]["contacts"] += 1
        for e in events:
            key = "registrations" if e.kind is PartnerEventKind.REGISTRATION else "payments"
            daily[e.occurred_at.date()][key] += 1

        return {
            "contacts": len(contacts),
            "new_contacts": len(new_clients),
            "replied": replied,
            "by_stage": by_stage,
            "registrations": len(reg_clients),
            "payments": len(pay_events),
            "revenue": round(sum(e.amount or 0.0 for e in pay_events), 2),
            "partner_payouts": round(sum(e.payout or 0.0 for e in events), 2),
            "conversion": {
                "contact_to_registration": ratio(len(reg_clients), len(contacts)),
                "registration_to_payment": ratio(len(pay_clients), len(reg_clients)),
            },
            "llm": await self.llm_totals(start, end, persona_id),
            "guard": await self.guard_counts(start, end, persona_id),
            "by_channel": by_channel,
            "daily": [{"date": d.isoformat(), **v} for d, v in sorted(daily.items())],
        }

    @staticmethod
    def has_exchange(items: list[tuple[datetime, Direction]]) -> bool:
        out_seen = False
        for _, direction in sorted(items, key=lambda x: x[0]):
            if direction is Direction.OUT:
                out_seen = True
            elif out_seen:
                return True
        return False

    async def message_rows(
        self, start: datetime, end: datetime, persona_id: int | None
    ) -> list[tuple[int, ChannelKind, Direction, datetime]]:
        stmt = (
            select(Dialog.client_id, Channel.kind, Message.direction, Message.created_at)
            .join(Dialog, Dialog.id == Message.dialog_id)
            .join(Channel, Channel.id == Dialog.channel_id)
            .join(Client, Client.id == Dialog.client_id)
            .where(Message.created_at >= start, Message.created_at < end)
        )
        if persona_id:
            stmt = stmt.where(Client.persona_id == persona_id)
        return [tuple(r) for r in (await self.session.execute(stmt)).all()]  # type: ignore[misc]

    async def new_clients(
        self, start: datetime, end: datetime, persona_id: int | None
    ) -> list[datetime]:
        stmt = select(Client.created_at).where(Client.created_at >= start, Client.created_at < end)
        if persona_id:
            stmt = stmt.where(Client.persona_id == persona_id)
        return list((await self.session.scalars(stmt)).all())

    async def event_rows(
        self, start: datetime, end: datetime, persona_id: int | None
    ) -> list[PartnerEvent]:
        stmt = select(PartnerEvent).where(
            PartnerEvent.occurred_at >= start, PartnerEvent.occurred_at < end
        )
        if persona_id:
            stmt = stmt.join(Client, Client.id == PartnerEvent.client_id).where(
                Client.persona_id == persona_id
            )
        return list((await self.session.scalars(stmt)).all())

    async def first_channel_kinds(self, client_ids: set[int]) -> dict[int, ChannelKind]:
        if not client_ids:
            return {}
        rows = await self.session.execute(
            select(Dialog.client_id, Channel.kind)
            .join(Channel, Channel.id == Dialog.channel_id)
            .where(Dialog.client_id.in_(client_ids))
            .order_by(Dialog.id.desc())
        )
        result: dict[int, ChannelKind] = {}
        for client_id, kind in rows.all():  # oldest dialog wins (ordered desc, overwritten)
            result[client_id] = kind
        return result

    async def guard_counts(
        self, start: datetime, end: datetime, persona_id: int | None
    ) -> dict[str, int]:
        """AI messages by the pre-send guard's final verdict."""
        stmt = (
            select(Message.guard_verdict, func.count(Message.id))
            .where(
                Message.created_at >= start, Message.created_at < end,
                Message.guard_verdict.is_not(None),
            )
            .group_by(Message.guard_verdict)
        )
        if persona_id:
            stmt = (
                stmt.join(Dialog, Dialog.id == Message.dialog_id)
                .join(Client, Client.id == Dialog.client_id)
                .where(Client.persona_id == persona_id)
            )
        counts = {"ok": 0, "rewritten": 0, "blocked": 0}
        for verdict, n in (await self.session.execute(stmt)).all():
            if verdict in counts:
                counts[verdict] = int(n)
        return counts

    async def llm_totals(
        self, start: datetime, end: datetime, persona_id: int | None
    ) -> dict[str, Any]:
        # Admin simulations (workflows "sim*") are test traffic, not part of the business numbers.
        stmt = select(LlmCall).where(
            LlmCall.created_at >= start, LlmCall.created_at < end, LlmCall.workflow.not_like("sim%")
        )
        if persona_id:
            stmt = (
                stmt.join(Dialog, Dialog.id == LlmCall.dialog_id)
                .join(Client, Client.id == Dialog.client_id)
                .where(Client.persona_id == persona_id)
            )
        calls = (await self.session.scalars(stmt)).all()
        return {
            "calls": len(calls),
            "cost_usd": round(sum(c.cost_usd or 0.0 for c in calls), 6),
            "tokens_in": sum(c.tokens_in or 0 for c in calls),
            "tokens_out": sum(c.tokens_out or 0 for c in calls),
        }
