"""Optional port capabilities. The Protocols in sim.ports.channel stay minimal; anything
beyond send/status/fetch is declared here and discovered via `port.capabilities`."""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from sim.ports.channel import SendResult


class Capability(StrEnum):
    DOWNLOAD_MEDIA = "download_media"   # port.download_media(ref) -> bytes
    PUBLISH_PHOTO = "publish_photo"     # port.publish_photo(path, caption) -> external id
    SEND_VOICE = "send_voice"           # port.send_voice(thread, ogg_opus, duration_s) -> SendResult
    SEND_ATTACHMENT = "send_attachment"  # port.send_attachment(thread, bytes, name, mime, caption)
    SEND_PHOTO = "send_photo"           # port.send_photo(thread, bytes, caption) -> SendResult
    SEND_VIDEO_NOTE = "send_video_note" # port.send_video_note(thread, bytes) -> SendResult
    CHAT_ACTION = "chat_action"          # port.send_chat_action(thread, "typing" | "record_voice")
    MARK_READ = "mark_read"              # port.mark_read(thread, external_message_id)
    # Incoming voice calls reach the worker: its connector takes `listen_calls(handler)` and
    # hands every call over as an IncomingCall (sim.services.calls answers it).
    ACCEPT_CALL = "accept_call"


TYPING = "typing"                  # "печатает…"
RECORD_VOICE = "record_voice"      # "записывает голосовое…"
CANCEL = "cancel"                  # clears the action at once (where the messenger supports it)


@runtime_checkable
class ChatActionSender(Protocol):
    """Shows the persona as typing / recording a voice note. Best effort: an indicator never
    fails a reply, so implementations swallow their errors. One call lasts a few seconds
    (Telegram ~5 s), callers repeat it while the work goes on."""

    async def send_chat_action(self, external_thread_id: str, action: str) -> None: ...


@runtime_checkable
class ReadMarker(Protocol):
    """Marks the client's messages up to this one as read (the client sees "прочитано"). Best
    effort like chat actions: implementations swallow their errors."""

    async def mark_read(self, external_thread_id: str, external_message_id: str) -> None: ...


@runtime_checkable
class MediaDownloader(Protocol):
    async def download_media(self, ref: str) -> bytes: ...


@runtime_checkable
class PhotoPublisher(Protocol):
    async def publish_photo(self, path: Path, caption: str) -> str: ...


@runtime_checkable
class VoiceSender(Protocol):
    """Sends an OGG/Opus voice note (the channel shows it as a playable voice message)."""

    async def send_voice(
        self, external_thread_id: str, audio: bytes, duration_s: float | None
    ) -> SendResult: ...


@runtime_checkable
class AttachmentSender(Protocol):
    async def send_attachment(
        self, external_thread_id: str, data: bytes, filename: str,
        mime: str, caption: str,
    ) -> SendResult: ...


@runtime_checkable
class PhotoSender(Protocol):
    async def send_photo(
        self, external_thread_id: str, data: bytes, caption: str,
    ) -> SendResult: ...


@runtime_checkable
class VideoNoteSender(Protocol):
    async def send_video_note(
        self, external_thread_id: str, data: bytes,
    ) -> SendResult: ...


class IncomingCall(Protocol):
    """A voice call ringing on a channel account. Audio is 16-bit little-endian mono PCM at
    `rate_hz`, in 10 ms frames both ways."""

    external_thread_id: str   # the caller, as the channel's dialogs name the thread
    rate_hz: int

    async def answer(self) -> None: ...

    def frames(self) -> AsyncIterator[bytes]:
        """The caller's audio until the call ends (either side hangs up)."""
        ...

    async def play(self, pcm: bytes) -> None:
        """One outgoing frame, sent at real-time pace by the caller of this method."""
        ...

    async def hangup(self) -> None: ...


CallHandler = Callable[[int, IncomingCall], Awaitable[None]]   # (channel id, call)


@runtime_checkable
class CallListener(Protocol):
    """Connector side of ACCEPT_CALL: the worker registers once, calls arrive per channel."""

    def listen_calls(self, handler: CallHandler) -> None: ...
