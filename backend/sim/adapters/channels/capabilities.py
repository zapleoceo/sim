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
