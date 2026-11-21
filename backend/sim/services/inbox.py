"""Incoming side of a chat (docs/reply.md, «Входящие»). The messenger process hands every
client message here: it is stored once (idempotent by chat + direction + external id), its
attachment goes to the media library, a `message_in` event is written and, after commit, the
attachment is queued for understanding and, when the AI may answer here (the AI gate,
`sim.domain.ai_gate`: AI on everywhere, no operator awaited), her reaction: a phone
check when her life lets her look (docs/attention.md), or with «Всегда сразу» a reply after
the burst pause as before.

First contact creates the client (AI off by default, spec §3.2), the identity and the chat. The
chat belongs to the persona of the channel the message came through (`channel.persona_id`);
a client who first wrote to another persona keeps his client row but gets a separate chat,
answered by this channel's persona (docs/personas.md)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Channel, Chat, ChatEvent, Client, ClientIdentity, Message, Persona
from sim.db.session import transaction
from sim.domain.ai_gate import ai_block
from sim.domain.attention import attention_settings
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
from sim.services.attention.schedule import check_after_notice, queue_check
from sim.services.deps import Deps
from sim.services.media_library import add_media, kind_from_mime

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


@dataclass(frozen=True)
class Reaction:
    """What a notification starts after commit: a reply after the burst pause («Всегда
    сразу», `reply_delay_s`) or her phone check (`check_at`; None = an earlier one covers it)."""

    persona_id: int
    now: datetime
    reply_delay_s: float | None = None
    check_at: datetime | None = None


@dataclass(frozen=True)
class _Stored:
    message_id: int
    chat_id: int
    media_id: int | None            # an attachment to understand
    reaction: Reaction | None       # None = AI is off somewhere: stored, not answered


def message_kind(attachment: InboundAttachment | None) -> MessageKind:
    if attachment is None:
        return MessageKind.TEXT
    return ATTACHMENT_KINDS.get(attachment.kind, MessageKind.FILE)


def understandable(attachment: InboundAttachment) -> bool:
    mime = (attachment.mime or "").lower()
    return mime.startswith(UNDERSTANDABLE_PREFIXES)


async def known_ids(session: AsyncSession, channel_id: int, peer_external_id: str,
                    external_ids: list[str]) -> set[str]:
    """Which of these incoming message ids of this person on this channel are already stored
    (the same key `_store` dedupes by: the chat of channel + identity, direction in)."""
    if not external_ids:
        return set()
    rows = await session.scalars(
        select(Message.external_id)
        .join(Chat, Chat.id == Message.chat_id)
        .join(Channel, Channel.id == Chat.channel_id)
        .join(ClientIdentity, (ClientIdentity.client_id == Chat.client_id)
              & (ClientIdentity.channel_kind == Channel.kind))
        .where(Chat.channel_id == channel_id,
               ClientIdentity.external_id == peer_external_id,
               Message.direction == Direction.IN,
               Message.external_id.in_(external_ids)))
    return {external_id for external_id in rows if external_id is not None}


class Inbox:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    async def known(self, channel_id: int, peer: Peer, external_ids: list[str]) -> set[str]:
        """The ids of these messages from `peer` the inbox already has: the catch-up skips them
        before downloading anything."""
        async with transaction(self.deps.sessions) as session:
            return await known_ids(session, channel_id, peer.external_id, external_ids)

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
        if stored.media_id is not None:
            await self.deps.tasks.understand(stored.media_id)
        await self._react(stored.chat_id, stored.reaction)
        return stored.message_id

    async def _react(self, chat_id: int, reaction: Reaction | None) -> None:
        """After commit: «Всегда сразу» answers like before (after the burst pause), otherwise
        her phone check is deferred (docs/attention.md)."""
        if reaction is None:
            return
        if reaction.reply_delay_s is not None:
            await self.deps.tasks.reply(chat_id, reaction.reply_delay_s)
        else:
            await queue_check(self.deps.tasks, reaction.persona_id, reaction.check_at,
                              reaction.now)

    async def _reaction(self, session: AsyncSession, persona: Persona | None,
                        client: Client | None, chat: Chat) -> Reaction | None:
        """How she reacts to a new notification in `chat` (inside its transaction)."""
        if persona is None or ai_block(persona, client, chat) is not None:
            return None
        now = self.deps.clock.now()
        if attention_settings(persona.attention).is_instant(chat.attention_instant):
            return Reaction(persona.id, now, reply_delay_s=float(persona.reply_delay_s))
        check_at = await check_after_notice(session, persona, now, self.deps.uniform())
        return Reaction(persona.id, now, check_at=check_at)

    async def _store(self, session: AsyncSession, msg: InboundMessage) -> _Stored | None:
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
        media_to_understand = None
        if media is not None and media.understanding == UnderstandingStatus.PENDING:
            media_to_understand = media.id
        return _Stored(message.id, chat.id, media_to_understand,
                       await self._reaction(session, persona, client, chat))

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
                        unread_count=0, peer_access_hash=peer.access_hash)
            session.add(chat)
            await session.flush()
        elif peer.access_hash and chat.peer_access_hash != peer.access_hash:
            chat.peer_access_hash = peer.access_hash    # this account's view of the client
        return chat

    async def edited(self, channel_id: int, peer: Peer, external_id: str, text: str) -> None:
        """An edit is journaled and is a notification too: in «Всегда сразу» nothing more
        happens (as before), otherwise her phone shows it at the next check."""
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
            reaction = await self._reaction(session, await session.get(Persona, chat.persona_id),
                                            await session.get(Client, chat.client_id), chat)
        if reaction is not None and reaction.reply_delay_s is None:
            await self._react(chat.id, reaction)

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
