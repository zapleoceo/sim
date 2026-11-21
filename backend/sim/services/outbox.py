"""Outgoing messages. A message is created `pending` here; after the caller commits it defers
`tasks.send(message.id, chat_id)`, and the messenger process reports the outcome back through
`mark_sent` / `mark_failed`, which also write the chat event (docs/context.md)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, Message
from sim.domain.enums import Author, Direction, EventKind, MessageKind, MessageStatus
from sim.events import append_event

FAILED_TEXT_PREVIEW = 300


async def enqueue_message(
    session: AsyncSession,
    *,
    chat_id: int,
    author: Author,
    kind: MessageKind,
    text: str = "",
    media_id: int | None = None,
    reply_run_id: int | None = None,
) -> Message:
    message = Message(
        chat_id=chat_id, direction=Direction.OUT, author=author, kind=kind, text=text,
        media_id=media_id, status=MessageStatus.PENDING, reply_run_id=reply_run_id,
    )
    session.add(message)
    await session.flush()
    return message


async def mark_sent(session: AsyncSession, message: Message, external_id: str,
                    at: datetime) -> None:
    message.status = MessageStatus.SENT
    message.external_id = external_id
    message.sent_at = at
    message.error = None
    chat = await session.get(Chat, message.chat_id)
    if chat is not None and (chat.last_message_at is None or chat.last_message_at < at):
        chat.last_message_at = at
    await append_event(
        session, chat_id=message.chat_id, kind=EventKind.MESSAGE_OUT,
        actor=str(message.author), message_id=message.id, media_id=message.media_id,
        reply_run_id=message.reply_run_id, data={"kind": str(message.kind)},
    )


async def mark_failed(session: AsyncSession, message: Message, error: str) -> None:
    message.status = MessageStatus.FAILED
    message.error = error
    await append_event(
        session, chat_id=message.chat_id, kind=EventKind.SEND_FAILED,
        actor=str(message.author), message_id=message.id, media_id=message.media_id,
        reply_run_id=message.reply_run_id,
        data={"kind": str(message.kind), "text": message.text[:FAILED_TEXT_PREVIEW],
              "error": error[:FAILED_TEXT_PREVIEW]},
    )
