"""Incoming side of a chat (docs/reply.md, «Входящие»). The messenger process hands every
client message here: it is stored once (idempotent by chat + direction + external id), its
attachment goes to the media library, a `message_in` event is written and, after commit, the
attachment is queued for understanding and the chat for a reply (when AI is on everywhere).

First contact creates the client (AI off by default, spec §3.2), the identity and the chat. The
chat belongs to the persona of the channel the message came through (`channel.persona_id`);
a client who first wrote to another persona keeps his client row but gets a separate chat,
answered by this channel's persona (docs/personas.md)."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Channel, Chat, ChatEvent, Client, ClientIdentity, Message, Persona
from sim.db.session import transaction
from sim.domain.enums import (
    Author,
    Direction,
    EventKind,
    MediaSource,
    MessageKind,
    MessageStatus,
    UnderstandingStatus,
)
from sim.events import append_event
from sim.ports import InboundAttachment, InboundMessage, Peer
from sim.services.deps import Deps
from sim.services.media_library import add_media, kind_from_mime
from sim.services.turn_state import ai_allowed

log = logging.getLogger(__name__)

ATTACHMENT_KINDS = {
    "photo": MessageKind.PHOTO,
    "video": MessageKind.VIDEO,
    "voice": MessageKind.VOICE,
    "audio": MessageKind.FILE,
    "video_note": MessageKind.VIDEO_NOTE,
    "file": MessageKind.FILE,
    "sticker": MessageKind.STICKER,
}
# Mime families a model can read (images, video, audio, documents).
UNDERSTANDABLE_PREFIXES = ("image/", "video/", "audio/", "application/pdf", "text/",
                           "application/vnd.openxmlformats-officedocument",
                           "application/msword", "application/rtf",
                           "application/vnd.oasis.opendocument")


class ChannelNotFound(LookupError):
    pass


def message_kind(attachment: InboundAttachment | None) -> MessageKind:
    if attachment is None:
        return MessageKind.TEXT
    return ATTACHMENT_KINDS.get(attachment.kind, MessageKind.FILE)


def understandable(attachment: InboundAttachment) -> bool:
    mime = (attachment.mime or "").lower()
    return mime.startswith(UNDERSTANDABLE_PREFIXES)


class Inbox:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    async def ingest(self, msg: InboundMessage) -> int | None:
        """Store one client message. Returns the new message id, or None for a duplicate."""
        try:
            async with transaction(self.deps.sessions) as session:
                stored = await self._store(session, msg)
        except IntegrityError:
            # A concurrent delivery of the same message won the race.
            log.warning("duplicate or racing message %s from %s", msg.external_id,
                        msg.peer.external_id)
            return None
        if stored is None:
            return None
        message_id, chat_id, media_id, reply_delay = stored
        if media_id is not None:
            await self.deps.tasks.understand(media_id)
        if reply_delay is not None:
            await self.deps.tasks.reply(chat_id, reply_delay)
        return message_id

    async def _store(self, session: AsyncSession,
                     msg: InboundMessage) -> tuple[int, int, int | None, float | None] | None:
        chat = await self.resolve_chat(session, msg.channel_id, msg.peer)
        if await self._find(session, chat.id, msg.external_id) is not None:
            return None
        now = self.deps.clock.now()
        media = None
        attachment = msg.attachment
        if attachment is not None:
            pending = understandable(attachment)
            media = await add_media(
                session, self.deps.storage, self.deps.tools, persona_id=None,
                data=attachment.data, mime=attachment.mime, file_name=attachment.file_name,
                source=MediaSource.INCOMING, kind=kind_from_mime(attachment.mime),
                understanding=UnderstandingStatus.PENDING if pending
                else UnderstandingStatus.SKIPPED,
            )
            if media.duration_s is None and attachment.duration_s is not None:
                media.duration_s = attachment.duration_s
            if media.width is None and attachment.width is not None:
                media.width, media.height = attachment.width, attachment.height
        message = Message(
            chat_id=chat.id, direction=Direction.IN, author=Author.CLIENT,
            kind=message_kind(attachment), text=msg.text or "",
            media_id=media.id if media is not None else None, status=MessageStatus.RECEIVED,
            external_id=msg.external_id, reply_to_external_id=msg.reply_to_external_id,
            sent_at=msg.sent_at,
        )
        session.add(message)
        await session.flush()
        await append_event(
            session, chat_id=chat.id, kind=EventKind.MESSAGE_IN, actor="client",
            message_id=message.id, media_id=message.media_id,
            data={"kind": str(message.kind)},
        )
        chat.unread_count = (chat.unread_count or 0) + 1
        chat.last_message_at = msg.sent_at or now
        client = await session.get(Client, chat.client_id)
        persona = await session.get(Persona, chat.persona_id)
        if client is not None:
            client.last_seen_at = now
        answer = ai_allowed(persona, client, chat)
        media_to_understand = None
        if media is not None and media.understanding == UnderstandingStatus.PENDING:
            media_to_understand = media.id
        reply_delay = float(persona.reply_delay_s) if answer and persona is not None else None
        return message.id, chat.id, media_to_understand, reply_delay

    async def _find(self, session: AsyncSession, chat_id: int,
                    external_id: str) -> Message | None:
        return (await session.execute(
            select(Message).where(Message.chat_id == chat_id,
                                  Message.direction == Direction.IN,
                                  Message.external_id == external_id)
        )).scalar_one_or_none()

    async def resolve_chat(self, session: AsyncSession, channel_id: int, peer: Peer) -> Chat:
        """Find or create the client (by messenger identity) and the chat on this channel."""
        channel = await session.get(Channel, channel_id)
        if channel is None:
            raise ChannelNotFound(f"channel {channel_id} not found")
        identity = (await session.execute(
            select(ClientIdentity).where(ClientIdentity.channel_kind == channel.kind,
                                         ClientIdentity.external_id == peer.external_id)
        )).scalar_one_or_none()
        if identity is None:
            client = Client(persona_id=channel.persona_id, name=peer.name or "",
                            username=peer.username, phone=peer.phone, ai_enabled=False)
            session.add(client)
            await session.flush()
            session.add(ClientIdentity(client_id=client.id, channel_kind=channel.kind,
                                       external_id=peer.external_id,
                                       access_hash=peer.access_hash))
            await session.flush()
        else:
            client = await session.get(Client, identity.client_id)
            if client is None:
                raise ChannelNotFound(f"identity {identity.id} has no client")
            if peer.access_hash and identity.access_hash != peer.access_hash:
                identity.access_hash = peer.access_hash
            # Fill what we did not know yet; never overwrite what the operator edited.
            if not client.name and peer.name:
                client.name = peer.name
            if not client.username and peer.username:
                client.username = peer.username
            if not client.phone and peer.phone:
                client.phone = peer.phone
        chat = (await session.execute(
            select(Chat).where(Chat.channel_id == channel.id, Chat.client_id == client.id)
        )).scalar_one_or_none()
        if chat is None:
            chat = Chat(persona_id=channel.persona_id, client_id=client.id,
                        channel_id=channel.id, ai_enabled=True, needs_operator=False,
                        unread_count=0)
            session.add(chat)
            await session.flush()
        return chat

    async def edited(self, channel_id: int, peer: Peer, external_id: str, text: str) -> None:
        async with transaction(self.deps.sessions) as session:
            chat = await self.resolve_chat(session, channel_id, peer)
            message = await self._find(session, chat.id, external_id)
            if message is None or message.text == text:
                return
            old = message.text
            message.text = text
            await append_event(session, chat_id=chat.id, kind=EventKind.MESSAGE_EDITED,
                               actor="client", message_id=message.id,
                               data={"old": old, "new": text})

    async def deleted(self, channel_id: int, external_ids: list[str]) -> None:
        """Telegram does not say whose chat a deletion belongs to: match by channel + ids."""
        if not external_ids:
            return
        async with transaction(self.deps.sessions) as session:
            already = select(ChatEvent.message_id).where(
                ChatEvent.kind == EventKind.MESSAGE_DELETED, ChatEvent.message_id.is_not(None))
            rows = list((await session.execute(
                select(Message).join(Chat, Chat.id == Message.chat_id)
                .where(Chat.channel_id == channel_id, Message.external_id.in_(external_ids),
                       Message.id.not_in(already))
                .order_by(Message.id)
            )).scalars())
            for message in rows:
                await append_event(
                    session, chat_id=message.chat_id, kind=EventKind.MESSAGE_DELETED,
                    actor="client", message_id=message.id, media_id=message.media_id,
                    data={"text": message.text, "direction": str(message.direction),
                          "kind": str(message.kind)},
                )
