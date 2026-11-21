"""Idempotent Telegram sends (docs/telegram.md, «Отправка»).

Telegram drops a repeated send when it carries the same `random_id`, but Telethon's
`send_message` / `send_file` pick a new random one on every call: a retry after a timeout (the
first attempt may have reached Telegram) would deliver the message twice. So SIM sends with the
raw `messages.SendMessageRequest` / `messages.SendMediaRequest` and a `random_id` derived from
our message id (`random_id`), the same on every retry. A repeated id answered with
RANDOM_ID_DUPLICATE means «already delivered»: `invoke` returns no id instead of raising.

The message text and caption are parsed by the client's parse mode and the file is turned into
input media by Telethon itself (`_parse_message_text`, `_file_to_media`), exactly as `send_message`
and `send_file` do, so voice notes, round videos and documents go out as before."""
from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Any

log = logging.getLogger(__name__)

RANDOM_ID_BITS = 63                 # a positive int64, as Telegram's `long`
DUPLICATE = "RANDOM_ID_DUPLICATE"


def random_id(channel_id: int, key: int | None, part: int = 0) -> int:
    """A stable random id for part `part` of our message `key` on this account; a fresh random
    one when the message has no key."""
    if key is None:
        return secrets.randbits(RANDOM_ID_BITS) or 1
    digest = hashlib.sha256(f"sim:{channel_id}:{key}:{part}".encode()).digest()
    return (int.from_bytes(digest[:8], "big") >> (64 - RANDOM_ID_BITS)) or 1


def is_duplicate(exc: BaseException) -> bool:
    """Telegram already has a message with this random id: the earlier attempt went through."""
    return (type(exc).__name__ == "RandomIdDuplicateError"
            or getattr(exc, "message", None) == DUPLICATE)


def updates_of(result: Any) -> list[Any]:
    from telethon import types

    if isinstance(result, types.UpdateShort):
        return [result.update]
    return list(getattr(result, "updates", None) or [])


def new_message(result: Any) -> Any | None:
    """The message Telegram created, when the answer carries it."""
    for update in updates_of(result):
        message = getattr(update, "message", None)
        if getattr(message, "id", None) is not None:
            return message
    return None


def sent_id(result: Any, request_random_id: int) -> int | None:
    """The Telegram id of the message sent with `request_random_id`, or None if not told."""
    from telethon import types

    if isinstance(result, types.UpdateShortSentMessage):
        return int(result.id)
    for update in updates_of(result):
        if (isinstance(update, types.UpdateMessageID)
                and update.random_id == request_random_id):
            return int(update.id)
    message = new_message(result)
    return int(message.id) if message is not None else None


async def text_request(client: Any, entity: Any, text: str, rid: int) -> Any:
    from telethon import functions

    message, entities = await client._parse_message_text(text, ())
    return functions.messages.SendMessageRequest(
        peer=entity, message=message, entities=entities or None, random_id=rid)


async def media_request(client: Any, entity: Any, file: Any, caption: str, rid: int,
                        **options: Any) -> Any:
    """`options` are what `send_file` takes for the media (voice_note, attributes...)."""
    from telethon import functions

    message, entities = await client._parse_message_text(caption, ())
    _, media, _ = await client._file_to_media(file, **options)
    if not media:
        raise TypeError(f"cannot send {getattr(file, 'name', 'the file')} as media")
    return functions.messages.SendMediaRequest(
        peer=entity, media=media, message=message, entities=entities or None, random_id=rid)


async def invoke(client: Any, request: Any) -> tuple[int | None, Any]:
    """Sends; returns (Telegram message id or None, the raw answer). A duplicate random id is a
    success with no id (the message was delivered by an earlier attempt)."""
    try:
        result = await client(request)
    except Exception as exc:
        if not is_duplicate(exc):
            raise
        log.info("telegram: random id %s was already sent, the retry is skipped",
                 request.random_id)
        return None, None
    return sent_id(result, request.random_id), result
