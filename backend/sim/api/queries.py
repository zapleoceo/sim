"""Read helpers shared by the routers: 404 lookup, chat rows with their last message, timeline
items joined with their message / media / call / generation (batched, no N+1)."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from litestar.exceptions import NotFoundException
from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api import views
from sim.db.models import (
    Base,
    Call,
    Channel,
    Chat,
    ChatEvent,
    Client,
    Generation,
    MediaAsset,
    Message,
    Persona,
)
from sim.services.persona_status import chat_statuses

Json = dict[str, Any]


async def get_or_404[M: Base](session: AsyncSession, model: type[M], row_id: int,
                              what: str) -> M:
    row = await session.get(model, row_id)
    if row is None:
        raise NotFoundException(detail=f"{what} не найден(а): {row_id}")
    return row


async def persona_or_404(session: AsyncSession, persona_id: int) -> Persona:
    """The persona of a `/personas/{id}/…` path (archived ones too: the owner may edit them)."""
    persona = await session.get(Persona, persona_id)
    if persona is None:
        raise NotFoundException(detail=f"Персонаж не найден: {persona_id}")
    return persona


def search_clients(stmt: Select[Any], q: str | None) -> Select[Any]:
    """Filter by a substring of the client's name or username (`@` ignored)."""
    needle = (q or "").strip().removeprefix("@")
    if not needle:
        return stmt
    return stmt.where(or_(Client.name.icontains(needle, autoescape=True),
                          Client.username.icontains(needle, autoescape=True)))


async def by_ids[M: Base](session: AsyncSession, model: type[M],
                          ids: Iterable[int | None]) -> dict[int, M]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = await session.scalars(select(model).where(model.id.in_(wanted)))
    return {row.id: row for row in rows}


async def last_messages(session: AsyncSession, chat_ids: Sequence[int]) -> dict[int, Message]:
    if not chat_ids:
        return {}
    rows = await session.scalars(
        select(Message).where(Message.chat_id.in_(chat_ids))
        .distinct(Message.chat_id).order_by(Message.chat_id, Message.id.desc()))
    return {m.chat_id: m for m in rows}


async def chat_rows(session: AsyncSession, pairs: Sequence[tuple[Chat, Client]],
                    now: datetime) -> list[Json]:
    """Chat rows with the last message, the persona that answers in each chat and what she is
    doing about it now (`persona_status`, team-only, batched)."""
    last = await last_messages(session, [chat.id for chat, _ in pairs])
    personas = await by_ids(session, Persona, (chat.persona_id for chat, _ in pairs))
    channels = await by_ids(session, Channel, (chat.channel_id for chat, _ in pairs))
    statuses = await chat_statuses(
        session, [(chat, client, personas.get(chat.persona_id)) for chat, client in pairs], now)
    return [views.chat_row(chat, client, last.get(chat.id), personas.get(chat.persona_id),
                           status=statuses[chat.id].as_json(),
                           channel=channels.get(chat.channel_id))
            for chat, client in pairs]


async def chat_row(session: AsyncSession, chat: Chat, now: datetime) -> Json:
    client = await get_or_404(session, Client, chat.client_id, "Клиент")
    return (await chat_rows(session, [(chat, client)], now))[0]


async def chats_of_client(session: AsyncSession, client: Client, now: datetime) -> list[Json]:
    chats = await session.scalars(
        select(Chat).where(Chat.client_id == client.id)
        .order_by(Chat.last_message_at.desc().nulls_last(), Chat.id.desc()))
    return await chat_rows(session, [(chat, client) for chat in chats], now)


async def timeline_items(session: AsyncSession, events: Sequence[ChatEvent]) -> list[Json]:
    messages = await by_ids(session, Message, (e.message_id for e in events))
    media = await by_ids(session, MediaAsset, (m.media_id for m in messages.values()))
    calls = await by_ids(session, Call, (e.call_id for e in events))
    generations = await by_ids(session, Generation, (e.generation_id for e in events))
    items = []
    for event in events:
        message = messages.get(event.message_id) if event.message_id else None
        items.append(views.timeline_item(
            event, message=message,
            media=media.get(message.media_id) if message and message.media_id else None,
            call=calls.get(event.call_id) if event.call_id else None,
            generation=generations.get(event.generation_id) if event.generation_id else None,
        ))
    return items


async def message_item(session: AsyncSession, message: Message, actor: str) -> Json:
    """The timeline item of one message: its latest journal event, or a pending item."""
    event = await session.scalar(
        select(ChatEvent).where(ChatEvent.message_id == message.id)
        .order_by(ChatEvent.id.desc()).limit(1))
    if event is not None:
        return (await timeline_items(session, [event]))[0]
    media = await session.get(MediaAsset, message.media_id) if message.media_id else None
    return views.pending_message_item(message, media, actor)
