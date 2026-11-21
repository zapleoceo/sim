"""Turning the AI on picks up what is already waiting (docs/reply.md).

Messages that arrived while the AI was off were stored without a reply turn. When an operator
switches the AI on (for a chat or for the whole client), every chat whose AI is now effectively
on and that has unanswered client messages gets a reply turn — otherwise the persona would stay
silent until the client writes again."""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, Client, Persona
from sim.services.queue import TaskQueue
from sim.services.turn_state import unanswered


async def chats_to_wake(session: AsyncSession, chat_ids: Iterable[int]) -> list[int]:
    """Chats among `chat_ids` with the AI on at every level and something left unanswered."""
    ids = list(chat_ids)
    if not ids:
        return []
    rows = (await session.execute(
        select(Chat.id)
        .join(Client, Client.id == Chat.client_id)
        .join(Persona, Persona.id == Chat.persona_id)
        .where(Chat.id.in_(ids), Chat.ai_enabled.is_(True), Client.ai_enabled.is_(True),
               Persona.ai_enabled.is_(True), Chat.needs_operator.is_(False))
        .order_by(Chat.id)
    )).scalars().all()
    return [chat_id for chat_id in rows if await unanswered(session, chat_id) is not None]


async def wake(tasks: TaskQueue, chat_ids: Iterable[int]) -> None:
    """Defer a reply turn for each chat. Call AFTER the switch is committed."""
    for chat_id in chat_ids:
        await tasks.reply(chat_id, 0)
