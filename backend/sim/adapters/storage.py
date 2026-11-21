"""Media files on the local disk (`SIM_MEDIA_DIR`, the shared docker volume /app/media).

Implements `MediaStoragePort`. A key is `<prefix>/<yyyy>/<mm>/<uuid hex>.<ext>` relative to the
root, e.g. `library/2026/11/3f2a….jpg`; the database keeps only the key (`media_asset.storage_key`).

- Keys are checked before any disk access: only `[a-z0-9_.-]` segments, no `..`, no absolute
  paths, and the resolved path must stay inside the root.
- A write goes to a temporary file in the target directory and is renamed into place, so a
  reader never sees half a file.
- Disk work runs in a thread (`asyncio.to_thread`); the event loop is never blocked."""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from pathlib import Path

from sim.clock import SystemClock
from sim.ports import ClockPort, PortError

# Extensions for the types SIM stores; anything else is kept as an opaque `.bin`.
EXTENSIONS: dict[str, str] = {
    "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif",
    "image/heic": "heic",
    "video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm",
    "audio/ogg": "ogg", "audio/opus": "opus", "audio/mpeg": "mp3", "audio/mp4": "m4a",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/webm": "weba",
    "application/pdf": "pdf", "text/plain": "txt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
}
PREFIX = re.compile(r"^[a-z0-9_-]+(/[a-z0-9_-]+)*$")
SEGMENT = re.compile(r"^[a-z0-9_-][a-z0-9_.-]*$")


class InvalidMediaKey(PortError):
    """The key or prefix could point outside the media root."""


class MediaNotFound(PortError):
    """No file under this key."""


def extension_for(mime: str) -> str:
    return EXTENSIONS.get(mime.split(";", 1)[0].strip().lower(), "bin")


def check_key(key: str) -> list[str]:
    """The key's path segments; raises InvalidMediaKey for anything that is not a plain
    relative path of safe names."""
    parts = key.split("/")
    if not key or any(not SEGMENT.match(part) or part in {".", ".."} for part in parts):
        raise InvalidMediaKey(f"invalid media key: {key!r}")
    return parts


class LocalMediaStorage:
    def __init__(self, root: str, *, clock: ClockPort | None = None) -> None:
        self.root = Path(root)
        self.clock = clock or SystemClock()

    def new_key(self, mime: str, prefix: str) -> str:
        if not PREFIX.match(prefix):
            raise InvalidMediaKey(f"invalid media prefix: {prefix!r}")
        now = self.clock.now()
        return f"{prefix}/{now:%Y}/{now:%m}/{uuid.uuid4().hex}.{extension_for(mime)}"

    async def put(self, data: bytes, *, mime: str, prefix: str) -> str:
        key = self.new_key(mime, prefix)
        await asyncio.to_thread(self._write, key, data)
        return key

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._read, key)

    async def delete(self, key: str) -> None:
        """Removes the file; a key with no file is not an error (delete is idempotent)."""
        await asyncio.to_thread(self._delete, key)

    # --- blocking helpers, always called in a worker thread --------------------------------

    def path(self, key: str) -> Path:
        """The absolute path of a key, guaranteed to be inside the root."""
        root = self.root.resolve()
        target = root.joinpath(*check_key(key)).resolve()
        if not target.is_relative_to(root) or target == root:
            raise InvalidMediaKey(f"invalid media key: {key!r}")
        return target

    def _write(self, key: str, data: bytes) -> None:
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.part")
        try:
            with partial.open("wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            partial.replace(target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

    def _read(self, key: str) -> bytes:
        try:
            return self.path(key).read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            raise MediaNotFound(f"media file not found: {key}") from None

    def _delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)
