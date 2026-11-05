"""Personas, strategies, channels."""
from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select

from sim.adapters.db.models import Channel, Dialog, Persona, Strategy
from sim.adapters.db.repositories.base import Repository
from sim.domain.enums import ChannelKind, SessionStatus


class PersonaRepo(Repository[Persona]):
    model = Persona

    async def by_name(self, name: str) -> Persona | None:
        return await self.session.scalar(select(Persona).where(Persona.name == name))


class StrategyRepo(Repository[Strategy]):
    model = Strategy

    async def by_name(self, name: str) -> Strategy | None:
        return await self.session.scalar(select(Strategy).where(Strategy.name == name))

    async def first(self) -> Strategy | None:
        return await self.session.scalar(select(Strategy).order_by(Strategy.id).limit(1))


class ChannelRepo(Repository[Channel]):
    model = Channel

    async def visible(self) -> Sequence[Channel]:
        res = await self.session.scalars(
            select(Channel).where(Channel.is_archived.is_(False)).order_by(Channel.id)
        )
        return res.all()

    async def pollable(self, kinds: Sequence[ChannelKind]) -> Sequence[Channel]:
        res = await self.session.scalars(
            select(Channel).where(
                Channel.kind.in_(list(kinds)),
                Channel.status == SessionStatus.ACTIVE,
                Channel.is_archived.is_(False),
            )
        )
        return res.all()

    async def has_dialogs(self, channel_id: int) -> bool:
        n = await self.session.scalar(
            select(func.count(Dialog.id)).where(Dialog.channel_id == channel_id)
        )
        return bool(n)
