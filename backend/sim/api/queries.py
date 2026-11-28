"""Read helpers shared by the routers: 404 lookup, chat rows with their last message and the
client's other chats with the same persona (`chats`, docs/frontend.md «Одна лента на клиента»),
timeline items joined with their message / media / call / generation, and the journal of several
chats of one person in one stream (batched, no N+1)."""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from litestar.exceptions import NotFoundException
from sqlalchemy import Select, func, or_, select, tuple_
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
from sim.domain.enums import EventKind
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


PHONE_SEARCH_MIN = 5            # digits typed before the search looks at phones


def search_clients(stmt: Select[Any], q: str | None) -> Select[Any]:
    """Filter by a substring of the client's name or username (`@` ignored) or, when the search
    has enough digits, of the phone with only its digits compared (`099 481-18` finds
    `+380994811889`)."""
    needle = (q or "").strip().removeprefix("@")
    if not needle:
        return stmt
    found = [Client.name.icontains(needle, autoescape=True),
             Client.username.icontains(needle, autoescape=True)]
    digits = "".join(ch for ch in needle if ch.isdigit())
    if len(digits) >= PHONE_SEARCH_MIN and not re.search(r"[^\d\s()+\-]", needle):
        phone_digits = func.regexp_replace(func.coalesce(Client.phone, ""), r"\D", "", "g")
        found.append(phone_digits.contains(digits))
    return stmt.where(or_(*found))


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


def person_key(chat: Chat) -> tuple[int, int]:
    """One person as one persona sees them: the chats of one client with one persona."""
    return chat.client_id, chat.persona_id


async def person_chats(session: AsyncSession, chats: Sequence[Chat]) -> dict[tuple[int, int],
                                                                            list[Json]]:
    """For each chat, every chat of the same client with the same persona (itself included),
    most recent first: its messenger, unread, when the client last wrote there."""
    keys = {person_key(chat) for chat in chats}
    if not keys:
        return {}
    rows = list(await session.scalars(
        select(Chat).where(tuple_(Chat.client_id, Chat.persona_id).in_(sorted(keys)))
        .order_by(Chat.last_message_at.desc().nulls_last(), Chat.id.desc())))
    channels = await by_ids(session, Channel, (c.channel_id for c in rows))
    last_in = dict((await session.execute(
        select(ChatEvent.chat_id, func.max(ChatEvent.created_at))
        .where(ChatEvent.chat_id.in_([c.id for c in rows]),
               ChatEvent.kind == EventKind.MESSAGE_IN)
        .group_by(ChatEvent.chat_id))).all())
    found: dict[tuple[int, int], list[Json]] = defaultdict(list)
    for chat in rows:
        found[person_key(chat)].append(
            views.person_chat(chat, channels.get(chat.channel_id), last_in.get(chat.id)))
    return found


async def chat_rows(session: AsyncSession, pairs: Sequence[tuple[Chat, Client]],
                    now: datetime) -> list[Json]:
    """Chat rows with the last message, the persona that answers in each chat, what she is
    doing about it now (`persona_status`, team-only) and the client's chats with her in every
    messenger (`chats`), batched."""
    last = await last_messages(session, [chat.id for chat, _ in pairs])
    persons = await person_chats(session, [chat for chat, _ in pairs])
    personas = await by_ids(session, Persona, (chat.persona_id for chat, _ in pairs))
    channels = await by_ids(session, Channel, (chat.channel_id for chat, _ in pairs))
    statuses = await chat_statuses(
        session, [(chat, client, personas.get(chat.persona_id)) for chat, client in pairs], now)
    return [views.chat_row(chat, client, last.get(chat.id), personas.get(chat.persona_id),
                           status=statuses[chat.id].as_json(),
                           channel=channels.get(chat.channel_id),
                           chats=persons.get(person_key(chat), []))
            for chat, client in pairs]


async def chat_row(session: AsyncSession, chat: Chat, now: datetime) -> Json:
    client = await get_or_404(session, Client, chat.client_id, "Клиент")
    return (await chat_rows(session, [(chat, client)], now))[0]


async def chats_of_client(session: AsyncSession, client: Client, now: datetime) -> list[Json]:
    chats = await session.scalars(
        select(Chat).where(Chat.client_id == client.id)
        .order_by(Chat.last_message_at.desc().nulls_last(), Chat.id.desc()))
    return await chat_rows(session, [(chat, client) for chat in chats], now)


async def stream_items(session: AsyncSession, chats: Sequence[Chat], *, before_id: int | None,
                       limit: int) -> list[Json]:
    """The journal of several chats of one person in one stream, newest first (event ids grow
    across chats); each item names its chat and messenger."""
    by_id = {chat.id: chat for chat in chats}
    if not by_id:
        return []
    channels = await by_ids(session, Channel, (chat.channel_id for chat in chats))
    stmt = select(ChatEvent).where(ChatEvent.chat_id.in_(list(by_id)))
    if before_id is not None:
        stmt = stmt.where(ChatEvent.id < before_id)
    events = list(await session.scalars(stmt.order_by(ChatEvent.id.desc()).limit(limit)))
    items = await timeline_items(session, events)
    return [{**item, "chat_id": event.chat_id,
             "channel": views.channel_brief(channels.get(by_id[event.chat_id].channel_id))}
            for event, item in zip(events, items, strict=True)]


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
