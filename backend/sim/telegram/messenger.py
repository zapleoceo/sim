"""`MessengerPort` over the connected Telethon clients of the runtime.

Every send of one account is serialized and spaced by `tg_min_send_interval_s` (a person does
not fire messages faster than that). Telegram errors become `PortError`:
- FloodWait / slow mode -> `FloodWait` (retryable, with the seconds Telegram asked to wait);
- 400 / 401 / 403 / 406 (bad peer, blocked by the user, privacy, bad media) -> permanent;
- anything else (server errors, timeouts, a dropped connection) -> retryable."""
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


def warn_if_not_round(sent: Any) -> None:
    """Telethon's Message.video_note is set only when Telegram stored a round video message;
    anything else (an ordinary video with a seek bar) is logged so it never goes unnoticed."""
    first = sent[0] if isinstance(sent, list) and sent else sent
    if getattr(first, "video_note", None) is None:
        log.warning("telegram: video note %s was not delivered as a round message",
                    getattr(first, "id", "?"))


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

    async def send(self, channel_id: int, message: OutboundMessage) -> str:
        """Returns the Telegram message id. A long text goes in several messages; the id is the
        first one's, and a failure after the first part is logged, not raised (a retry would
        duplicate what was delivered)."""
        client, entity = await self.address(channel_id, message.peer)
        if message.kind == "text":
            return await self.send_text(channel_id, client, entity, message.text)
        if not message.data:
            raise PortError(f"{message.kind} has no file to send")
        sent = await self.throttled(channel_id, lambda: self.send_media(client, entity, message))
        return str(int(sent.id))

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

    async def send_text(self, channel_id: int, client: Any, entity: Any, text: str) -> str:
        first: str | None = None
        for index, part in enumerate(chunks(text)):
            try:
                sent = await self.throttled(
                    channel_id, lambda part=part: client.send_message(entity, part))
            except PortError:
                if index == 0:
                    raise
                log.warning("telegram partial delivery: text part %d failed", index + 1)
                break
            first = first or str(int(sent.id))
        assert first is not None
        return first

    @staticmethod
    async def send_media(client: Any, entity: Any, message: OutboundMessage) -> Any:
        from telethon import types

        file = io.BytesIO(message.data or b"")
        file.name = file_name(message)
        caption = message.text[:CAPTION_LIMIT]
        if message.kind == "voice":
            duration = max(1, round(message.duration_s or 1))
            return await client.send_file(
                entity, file, voice_note=True, caption=caption,
                attributes=[types.DocumentAttributeAudio(duration=duration, voice=True)])
        if message.kind == "video_note":
            # A true round message: the flag plus an explicit round video attribute, so Telegram
            # never shows it as an ordinary square video.
            duration = max(1, round(message.duration_s or 1))
            sent = await client.send_file(
                entity, file, video_note=True,
                attributes=[types.DocumentAttributeVideo(
                    duration=duration, w=VIDEO_NOTE_SIDE, h=VIDEO_NOTE_SIDE,
                    round_message=True)])
            warn_if_not_round(sent)
            return sent
        if message.kind == "video":
            return await client.send_file(entity, file, caption=caption, supports_streaming=True)
        if message.kind == "photo":
            return await client.send_file(entity, file, caption=caption)
        return await client.send_file(entity, file, caption=caption, force_document=True)

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
        return result if self.is_message(result) else self.first_of(result)

    @staticmethod
    def is_message(result: Any) -> bool:
        return getattr(result, "id", None) is not None

    @staticmethod
    def first_of(result: Any) -> Any:
        """send_file may return a list (album); the first message identifies the send."""
        if isinstance(result, list) and result:
            return result[0]
        raise PortError("telegram returned no message")
