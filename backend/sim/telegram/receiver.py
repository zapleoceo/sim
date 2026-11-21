"""Incoming Telegram updates -> `Inbox`.

Only private chats with people are taken (no groups, channels, bots, Telegram's service
account). Own outgoing messages are ignored: what SIM sends is recorded by the send task, and
what someone types on the phone outside SIM is not part of the agent's chat.

A message id of a private chat is unique within the account, so `external_id` is the plain
Telegram message id: a deletion update (which carries ids only, no chat) maps straight to it.

Messages of one person are handed to the inbox strictly in arrival order (a per-person lock):
Telethon runs every update in its own task, and an album or a quick burst must not be stored
out of order because one photo downloads slower."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from sim.ports import InboundAttachment, InboundMessage, Peer
from sim.telegram.peers import is_person, peer_from_user

log = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024

DEFAULT_MIME = {
    "photo": "image/jpeg", "voice": "audio/ogg", "video": "video/mp4", "video_note": "video/mp4",
    "sticker": "image/webp", "audio": "audio/mpeg", "file": "application/octet-stream",
}


class InboxPort(Protocol):
    async def ingest(self, msg: InboundMessage) -> int | None: ...
    async def edited(self, channel_id: int, peer: Peer, external_id: str, text: str) -> None: ...
    async def deleted(self, channel_id: int, external_ids: list[str]) -> None: ...


def attachment_kind(message: Any) -> str | None:
    """photo | sticker | voice | video_note | video | audio | file, or None (text, link preview,
    location, poll...). Order matters: a video sticker is also a video, a voice note is audio."""
    if getattr(message, "web_preview", None) is not None:
        return None
    checks = (
        ("photo", "photo"), ("sticker", "sticker"), ("voice", "voice"),
        ("video_note", "video_note"), ("video", "video"), ("gif", "video"), ("audio", "audio"),
        ("document", "file"),
    )
    for attr, kind in checks:
        if getattr(message, attr, None):
            return kind
    return None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and value else None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) and value else None


async def download_attachment(message: Any, max_bytes: int = MAX_DOWNLOAD_BYTES,
                              ) -> InboundAttachment | None:
    """The file of a message with its metadata. Data is empty when the file is over the limit
    or could not be downloaded: the inbox still records that something was sent."""
    kind = attachment_kind(message)
    if kind is None:
        return None
    file = getattr(message, "file", None)
    mime = str(getattr(file, "mime_type", "") or "") or DEFAULT_MIME[kind]
    size = int(getattr(file, "size", 0) or 0)
    data = b""
    if size > max_bytes:
        log.info("telegram attachment of %s bytes is over the limit, not downloaded", size)
    else:
        try:
            raw = await message.download_media(file=bytes)
            data = bytes(raw or b"")
        except Exception as exc:
            log.warning("telegram attachment download failed: %s", type(exc).__name__)
        if len(data) > max_bytes:
            data = b""
    return InboundAttachment(
        kind=kind, data=data, mime=mime, file_name=str(getattr(file, "name", "") or ""),
        duration_s=_number(getattr(file, "duration", None)),
        width=_int(getattr(file, "width", None)), height=_int(getattr(file, "height", None)),
    )


def sent_at(message: Any) -> datetime:
    date = getattr(message, "date", None)
    if not isinstance(date, datetime):
        return datetime.now(UTC)
    return date if date.tzinfo else date.replace(tzinfo=UTC)


async def build_inbound(channel_id: int, peer: Peer, message: Any) -> InboundMessage:
    reply_to = getattr(message, "reply_to_msg_id", None)
    return InboundMessage(
        channel_id=channel_id, peer=peer, external_id=str(int(message.id)),
        text=str(getattr(message, "message", "") or ""), sent_at=sent_at(message),
        attachment=await download_attachment(message),
        reply_to_external_id=str(reply_to) if reply_to else None,
    )


class Receiver:
    """Telethon event handlers of every connected account; attach with `attach` (a runtime
    hook)."""

    def __init__(self, inbox: InboxPort) -> None:
        self.inbox = inbox
        self.locks: dict[tuple[int, int], asyncio.Lock] = {}

    async def attach(self, channel_id: int, client: Any) -> None:
        from telethon import events

        def bind(handler: Callable[[int, Any], Awaitable[None]]) -> Callable[[Any], Any]:
            async def on_event(event: Any) -> None:
                await handler(channel_id, event)
            return on_event

        client.add_event_handler(bind(self.on_new_message), events.NewMessage(incoming=True))
        client.add_event_handler(bind(self.on_edited), events.MessageEdited(incoming=True))
        client.add_event_handler(bind(self.on_deleted), events.MessageDeleted())

    def lock_for(self, channel_id: int, event: Any) -> asyncio.Lock:
        key = (channel_id, int(getattr(event, "chat_id", 0) or 0))
        return self.locks.setdefault(key, asyncio.Lock())

    async def person(self, event: Any) -> Peer | None:
        if not getattr(event, "is_private", False):
            return None
        sender = await event.get_sender()
        return peer_from_user(sender) if is_person(sender) else None

    async def on_new_message(self, channel_id: int, event: Any) -> None:
        async with self.lock_for(channel_id, event):     # taken before any await: FIFO order
            try:
                peer = await self.person(event)
                if peer is None:
                    return
                await self.inbox.ingest(await build_inbound(channel_id, peer, event.message))
            except Exception:
                log.exception("telegram channel %s: incoming message not stored", channel_id)

    async def on_edited(self, channel_id: int, event: Any) -> None:
        async with self.lock_for(channel_id, event):
            try:
                peer = await self.person(event)
                if peer is None:
                    return
                message = event.message
                await self.inbox.edited(channel_id, peer, str(int(message.id)),
                                        str(getattr(message, "message", "") or ""))
            except Exception:
                log.exception("telegram channel %s: edit not stored", channel_id)

    async def on_deleted(self, channel_id: int, event: Any) -> None:
        """Private-chat deletions carry no chat id; channels/groups (with a chat id) are not
        ours."""
        if getattr(event, "chat_id", None) is not None:
            return
        ids = [str(int(i)) for i in getattr(event, "deleted_ids", None) or []]
        if not ids:
            return
        try:
            await self.inbox.deleted(channel_id, ids)
        except Exception:
            log.exception("telegram channel %s: deletion not stored", channel_id)
