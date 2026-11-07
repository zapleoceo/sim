"""Bounded cross-channel message and LLM activity reads, with shared context filters."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import (
    Channel,
    Client,
    Dialog,
    LlmCall,
    Message,
    OutboundSendIntent,
    Persona,
)
from sim.domain.enums import Author, ChannelKind, Delivery, Direction


@dataclass(frozen=True)
class ActivityFilter:
    client_id: int | None = None
    persona_id: int | None = None
    channel_id: int | None = None
    channel_kind: ChannelKind | None = None
    q: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    direction: Direction | None = None
    author: Author | None = None
    delivery: Delivery | None = None
    workflow: str | None = None
    model: str | None = None
    provider: str | None = None
    has_error: bool | None = None


def literal_pattern(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class ActivityRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def context(stmt: Select[*tuple[Any, ...]], query: ActivityFilter, model: type[Message] | type[LlmCall]) -> Select[*tuple[Any, ...]]:
        for column, value in (
            (Client.id, query.client_id), (Client.persona_id, query.persona_id),
            (Channel.id, query.channel_id), (Channel.kind, query.channel_kind),
        ):
            if value is not None:
                stmt = stmt.where(column == value)
        if query.start is not None:
            stmt = stmt.where(model.created_at >= query.start)
        if query.end is not None:
            stmt = stmt.where(model.created_at < query.end)
        return stmt

    async def page(self, stmt: Select[*tuple[Any, ...]], model: type[Message] | type[LlmCall], limit: int, offset: int):
        total = await self.session.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = (await self.session.execute(
            stmt.order_by(model.created_at.desc(), model.id.desc()).limit(limit).offset(offset)
        )).all()
        return rows, total or 0

    async def messages(self, query: ActivityFilter, limit: int, offset: int):
        stmt = (
            select(Message, Client, Persona, Channel, OutboundSendIntent.sent_at)
            .join(Dialog, Dialog.id == Message.dialog_id)
            .join(Client, Client.id == Dialog.client_id)
            .join(Persona, Persona.id == Client.persona_id)
            .join(Channel, Channel.id == Dialog.channel_id)
            .outerjoin(OutboundSendIntent, OutboundSendIntent.message_id == Message.id)
        )
        stmt = self.context(stmt, query, Message)
        for column, value in ((Message.direction, query.direction), (Message.author, query.author),
                              (Message.delivery, query.delivery)):
            if value is not None:
                stmt = stmt.where(column == value)
        if query.q:
            pattern = literal_pattern(query.q.strip())
            stmt = stmt.where(or_(Message.text.ilike(pattern, escape="\\"), Client.display_name.ilike(pattern, escape="\\")))
        return await self.page(stmt, Message, limit, offset)

    async def llm(self, query: ActivityFilter, limit: int, offset: int):
        stmt = (
            select(LlmCall, Client, Persona, Channel)
            .outerjoin(Dialog, Dialog.id == LlmCall.dialog_id)
            .outerjoin(Client, Client.id == Dialog.client_id)
            .outerjoin(Persona, Persona.id == Client.persona_id)
            .outerjoin(Channel, Channel.id == Dialog.channel_id)
        )
        stmt = self.context(stmt, query, LlmCall)
        for column, value in ((LlmCall.workflow, query.workflow), (LlmCall.model, query.model),
                              (LlmCall.provider, query.provider)):
            if value is not None:
                stmt = stmt.where(column == value)
        if query.has_error is not None:
            has_error = LlmCall.error.is_not(None) & (LlmCall.error != "")
            stmt = stmt.where(has_error if query.has_error else ~has_error)
        if query.q:
            pattern = literal_pattern(query.q.strip())
            stmt = stmt.where(or_(*(column.ilike(pattern, escape="\\") for column in
                                   (LlmCall.model, LlmCall.provider, LlmCall.workflow))))
        return await self.page(stmt, LlmCall, limit, offset)
