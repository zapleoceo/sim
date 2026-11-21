"""Which messenger a chat or a message goes through, for the task queue (sim/tasks/queue.py):
Telegram jobs go to the telegram process, Instagram and WhatsApp jobs to the `channels` queue
(sim/domain/channels.py). A chat never changes its channel, so its kind is cached."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.db.models import Channel, Chat, Message
from sim.domain.channels import ChannelSpec, spec_of
from sim.domain.enums import ChannelKind

CACHE_LIMIT = 10_000


class DbChannelRouter:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions
        self._chats: dict[int, ChannelKind] = {}

    async def for_chat(self, chat_id: int) -> ChannelSpec:
        kind = self._chats.get(chat_id)
        if kind is None:
            async with self.sessions() as session:
                found = await session.scalar(
                    select(Channel.kind).join(Chat, Chat.channel_id == Channel.id)
                    .where(Chat.id == chat_id))
            kind = ChannelKind(found) if found is not None else ChannelKind.TELEGRAM_USER
            if found is not None:
                self._remember(chat_id, kind)
        return spec_of(kind)

    async def for_message(self, message_id: int) -> ChannelSpec:
        async with self.sessions() as session:
            chat_id = await session.scalar(select(Message.chat_id)
                                           .where(Message.id == message_id))
        if chat_id is None:
            return spec_of(ChannelKind.TELEGRAM_USER)
        return await self.for_chat(chat_id)

    def _remember(self, chat_id: int, kind: ChannelKind) -> None:
        if len(self._chats) >= CACHE_LIMIT:
            self._chats.clear()
        self._chats[chat_id] = kind
