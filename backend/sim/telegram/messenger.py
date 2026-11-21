"""`MessengerPort` over the connected Telethon clients of the runtime.

Every send of one account is serialized and spaced by `tg_min_send_interval_s` (a person does
not fire messages faster than that). Telegram errors become `PortError`:
- FloodWait / slow mode -> `FloodWait` (retryable, with the seconds Telegram asked to wait);
- 400 / 401 / 403 / 406 (bad peer, blocked by the user, privacy, bad media) -> permanent;
- anything else (server errors, timeouts, a dropped connection) -> retryable.

A retryable error may come after Telegram already took the message (a timeout on the answer),
so every send is idempotent: it carries a `random_id` derived from our message id
(`OutboundMessage.key`, sim/telegram/sending.py) and a retry Telegram recognizes as a duplicate
counts as sent."""
from __future__ import annotations

import asyncio
import io
import logging
import mimetypes
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sim.domain.presence import ChatAction
from sim.ports import OutboundMessage, Peer, PortError
from sim.telegram.peers import input_peer
from sim.telegram.sending import invoke, media_request, new_message, random_id, text_request

log = logging.getLogger(__name__)

TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024
PERMANENT_RPC_CODES = frozenset({400, 401, 403, 406})

# Every chat action SIM shows (sim/domain/presence.py) -> Telethon `SendMessage*Action` class.
CHAT_ACTIONS: dict[str, str] = {
    ChatAction.TYPING: "SendMessageTypingAction",
    ChatAction.RECORD_AUDIO: "SendMessageRecordAudioAction",
    ChatAction.RECORD_ROUND: "SendMessageRecordRoundAction",
    ChatAction.UPLOAD_PHOTO: "SendMessageUploadPhotoAction",
    ChatAction.UPLOAD_VIDEO: "SendMessageUploadVideoAction",
    ChatAction.UPLOAD_DOCUMENT: "SendMessageUploadDocumentAction",
    ChatAction.CANCEL: "SendMessageCancelAction",
}
_UPLOAD_ACTIONS = frozenset({ChatAction.UPLOAD_PHOTO, ChatAction.UPLOAD_VIDEO,
                             ChatAction.UPLOAD_DOCUMENT})
VIDEO_NOTE_SIDE = 640           # sim.adapters.media_tools cuts round videos to this square

DEFAULT_NAMES = {"photo": "photo.jpg", "video": "video.mp4", "voice": "voice.ogg",
                 "video_note": "video_note.mp4", "file": "file.bin"}


class FloodWait(PortError):
    """Telegram asked to wait `seconds` before the next request."""

    def __init__(self, seconds: int) -> None:
        super().__init__(f"telegram flood wait {seconds}s", retryable=True)
        self.seconds = seconds


class ClientSource(Protocol):
    def client(self, channel_id: int) -> Any: ...


def to_port_error(exc: BaseException) -> PortError:
    """One mapping of Telethon/network errors for every send path (callers handle PortError
    itself before calling this)."""
    seconds = getattr(exc, "seconds", None)
    if isinstance(seconds, int):                      # FloodWait, SlowModeWait, FloodPremiumWait
        return FloodWait(max(0, seconds))
    text = f"{type(exc).__name__}: {str(exc)[:300]}"
    code = getattr(exc, "code", None)
    if isinstance(code, int) and code in PERMANENT_RPC_CODES:
        return PortError(text, retryable=False)
    if isinstance(exc, ValueError | TypeError):        # Telethon: unknown entity, bad input
        return PortError(text, retryable=False)
    return PortError(text, retryable=True)


def chunks(text: str, limit: int = TEXT_LIMIT) -> list[str]:
    """Splits a long text on line breaks / spaces where possible."""
    out: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = max(rest.rfind("\n", 0, limit), rest.rfind(" ", 0, limit))
        if cut <= limit // 2:
            cut = limit
        out.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest or not out:
        out.append(rest)
    return out


def file_name(message: OutboundMessage) -> str:
    if message.file_name:
        return message.file_name
    default = DEFAULT_NAMES.get(message.kind, "file.bin")
    if message.kind in ("photo", "file") and message.mime:
        ext = mimetypes.guess_extension(message.mime)
        if ext:
            return default.rsplit(".", 1)[0] + ext
    return default


def chat_action(action: str) -> Any:
    from telethon import types

    name = CHAT_ACTIONS.get(action, CHAT_ACTIONS["typing"])
    cls = getattr(types, name)
    return cls(progress=0) if action in _UPLOAD_ACTIONS else cls()


def media_options(message: OutboundMessage) -> dict[str, Any]:
    """What `send_file` is given for each kind: a voice note with its duration, a true round
    message (the flag plus an explicit round video attribute, so Telegram never shows it as an
    ordinary square video), a streamable video, a photo, a document."""
    from telethon import types

    duration = max(1, round(message.duration_s or 1))
    if message.kind == "voice":
        return {"voice_note": True,
                "attributes": [types.DocumentAttributeAudio(duration=duration, voice=True)]}
    if message.kind == "video_note":
        return {"video_note": True,
                "attributes": [types.DocumentAttributeVideo(
                    duration=duration, w=VIDEO_NOTE_SIDE, h=VIDEO_NOTE_SIDE,
                    round_message=True)]}
    if message.kind == "video":
        return {"supports_streaming": True}
    if message.kind == "photo":
        return {}
    return {"force_document": True}


def caption_of(message: OutboundMessage) -> str:
    return "" if message.kind == "video_note" else message.text[:CAPTION_LIMIT]


def is_round(message: Any) -> bool:
    document = getattr(getattr(message, "media", None), "document", None)
    return any(getattr(attr, "round_message", False)
               for attr in getattr(document, "attributes", None) or [])


def warn_if_not_round(result: Any) -> None:
    """A video note Telegram stored as anything but a round message (an ordinary video with a
    seek bar) is logged so it never goes unnoticed. Nothing to check when the answer carries no
    message (a duplicate retry)."""
    message = new_message(result)
    if message is not None and not is_round(message):
        log.warning("telegram: video note %s was not delivered as a round message", message.id)


class TelegramMessenger:
    def __init__(
        self, runtime: ClientSource, *, min_interval_s: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.runtime = runtime
        self.interval = min_interval_s
        self.sleep = sleep
        self.mono = monotonic
        self.locks: dict[int, asyncio.Lock] = {}
        self.last_send: dict[int, float] = {}

    # --- MessengerPort -------------------------------------------------------------------

    async def send(self, channel_id: int, message: OutboundMessage) -> str | None:
        """Returns the Telegram message id, or None when Telegram did not say it (a retry of a
        message an earlier attempt delivered). A long text goes in several messages; the id is
        the first one's, and a failure after the first part is logged, not raised."""
        client, entity = await self.address(channel_id, message.peer)
        if message.kind == "text":
            return await self.send_text(channel_id, client, entity, message)
        if not message.data:
            raise PortError(f"{message.kind} has no file to send")
        rid = random_id(channel_id, message.key)
        sent_id = await self.throttled(
            channel_id, lambda: self.send_media(client, entity, message, rid))
        return None if sent_id is None else str(sent_id)

    async def set_typing(self, channel_id: int, peer: Peer, action: str) -> None:
        """Best effort: a chat action that fails never fails the send."""
        try:
            from telethon import functions

            client = self.runtime.client(channel_id)
            entity = await input_peer(client, peer)
            await client(functions.messages.SetTypingRequest(peer=entity,
                                                             action=chat_action(action)))
        except Exception as exc:
            log.debug("telegram chat action failed: %s", type(exc).__name__)

    async def mark_read(self, channel_id: int, peer: Peer, external_id: str) -> None:
        """Marks the chat read up to this incoming message (the client sees two ticks)."""
        client, entity = await self.address(channel_id, peer)
        try:
            await client.send_read_acknowledge(entity, max_id=int(external_id))
        except Exception as exc:
            raise to_port_error(exc) from exc

    # --- sending -------------------------------------------------------------------------

    async def address(self, channel_id: int, peer: Peer) -> tuple[Any, Any]:
        """The account's client and the person's input peer."""
        client = self.runtime.client(channel_id)          # PortError(retryable) if offline
        try:
            return client, await input_peer(client, peer)
        except Exception as exc:
            raise to_port_error(exc) from exc

    async def send_text(self, channel_id: int, client: Any, entity: Any,
                        message: OutboundMessage) -> str | None:
        """Every part has its own stable random id, so a retry re-sends only what is missing."""
        first: int | None = None
        for index, part in enumerate(chunks(message.text)):
            rid = random_id(channel_id, message.key, index)

            async def send(part: str = part, rid: int = rid) -> int | None:
                request = await text_request(client, entity, part, rid)
                return (await invoke(client, request))[0]

            try:
                sent_id = await self.throttled(channel_id, send)
            except PortError:
                if index == 0:
                    raise
                log.warning("telegram partial delivery: text part %d failed", index + 1)
                break
            if index == 0:
                first = sent_id
        return None if first is None else str(first)

    @staticmethod
    async def send_media(client: Any, entity: Any, message: OutboundMessage,
                         rid: int) -> int | None:
        file = io.BytesIO(message.data or b"")
        file.name = file_name(message)
        request = await media_request(client, entity, file, caption_of(message), rid,
                                      **media_options(message))
        sent_id, result = await invoke(client, request)
        if message.kind == "video_note":
            warn_if_not_round(result)
        return sent_id

    async def throttled(self, channel_id: int, send: Callable[[], Awaitable[Any]]) -> Any:
        """At most one send per account per `min_interval_s`, serialized; errors mapped."""
        lock = self.locks.setdefault(channel_id, asyncio.Lock())
        async with lock:
            last = self.last_send.get(channel_id)
            if last is not None:
                wait = self.interval - (self.mono() - last)
                if wait > 0:
                    await self.sleep(wait)
            try:
                result = await send()
            except Exception as exc:
                raise to_port_error(exc) from exc
            finally:
                self.last_send[channel_id] = self.mono()
        return result
