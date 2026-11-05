"""Optional port capabilities. The Protocols in sim.ports.channel stay minimal; anything
beyond send/status/fetch is declared here and discovered via `port.capabilities`."""
from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable


class Capability(StrEnum):
    DOWNLOAD_MEDIA = "download_media"   # port.download_media(ref) -> bytes
    PUBLISH_PHOTO = "publish_photo"     # port.publish_photo(path, caption) -> external id


@runtime_checkable
class MediaDownloader(Protocol):
    async def download_media(self, ref: str) -> bytes: ...


@runtime_checkable
class PhotoPublisher(Protocol):
    async def publish_photo(self, path: Path, caption: str) -> str: ...
