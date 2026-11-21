"""Turning the AI on picks up what is already waiting (docs/reply.md).

Messages that arrived while the AI was off were stored without anyone looking at them. When an
operator switches the AI on (for a chat or for the whole client), every chat whose AI is now on
at every level, without an operator flag and with unanswered client messages, counts as a fresh
notification: the persona gets a phone check by her life (docs/attention.md), exactly as if the
messages had just arrived. Otherwise she would stay silent until the client writes again."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, Client, Persona
from sim.services.attention.schedule import check_after_notice, queue_check
from sim.services.turn_state import unanswered


@dataclass(frozen=True)
class Wake:
    """A phone check `request_check` accepted: defer it after the commit."""

    persona_id: int
    due: datetime


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


async def plan_wakes(session: AsyncSession, deps: Any, chat_ids: Iterable[int]) -> list[Wake]:
    """Before the commit: ask each waiting chat's persona for a phone check (one per persona)."""
    now = deps.clock.now()
    wakes: list[Wake] = []
    seen: set[int] = set()
    for chat_id in await chats_to_wake(session, chat_ids):
        chat = await session.get(Chat, chat_id)
        if chat is None or chat.persona_id in seen:
            continue
        seen.add(chat.persona_id)
        persona = await session.get(Persona, chat.persona_id)
        if persona is None:
            continue
        due = await check_after_notice(session, persona, now, deps.uniform())
        if due is not None:
            wakes.append(Wake(persona.id, due))
    return wakes


async def wake(deps: Any, wakes: Iterable[Wake]) -> None:
    """After the commit: defer the accepted phone checks."""
    now = deps.clock.now()
    for item in wakes:
        await queue_check(deps.tasks, item.persona_id, item.due, now)
