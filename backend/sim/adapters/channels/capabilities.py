"""Optional port capabilities. The Protocols in sim.ports.channel stay minimal; anything
beyond send/status/fetch is declared here and discovered via `port.capabilities`."""
from __future__ import annotations

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


TYPING = "typing"                  # "печатает…"
RECORD_VOICE = "record_voice"      # "записывает голосовое…"


@runtime_checkable
class ChatActionSender(Protocol):
    """Shows the persona as typing / recording a voice note. Best effort: an indicator never
    fails a reply, so implementations swallow their errors. One call lasts a few seconds
    (Telegram ~5 s), callers repeat it while the work goes on."""

    async def send_chat_action(self, external_thread_id: str, action: str) -> None: ...


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
