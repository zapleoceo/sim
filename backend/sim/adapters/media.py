"""Local media store (downloaded voice / images).

Bytes are fetched through the channel port's `download_media` (a Telegram file id or a WhatsApp
message id is not a URL); this module only keeps them on disk.

Files are stored under settings.media_dir with random names and served only through the
authenticated /api/media/{name} route; the original URL is not kept (a channel file URL may
embed a bot token)."""
from __future__ import annotations

import re
import secrets
from pathlib import Path

SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{8,64}\.[A-Za-z0-9]{1,8}$")

# Media the admin may show inline (audio, images, video). SVG is left out on purpose: it can carry
# script. Everything else is stored with a neutral suffix and only ever served as a download.
INLINE_TYPES: dict[str, str] = {
    "ogg": "audio/ogg", "oga": "audio/ogg", "opus": "audio/ogg", "mp3": "audio/mpeg",
    "m4a": "audio/mp4", "aac": "audio/aac", "wav": "audio/wav", "flac": "audio/flac",
    "webm": "audio/webm", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "webp": "image/webp", "gif": "image/gif", "mp4": "video/mp4",
}
DOWNLOAD_SUFFIXES = frozenset({"txt", "csv", "json", "md", "pdf", "bin"})


def safe_suffix(suffix: str) -> str:
    """Only known media and plain-data suffixes are kept; anything else (html, svg, xml, js…)
    becomes `bin`, so a stored file can never be served as a page."""
    clean = re.sub(r"[^A-Za-z0-9]", "", suffix).lower()[:8]
    return clean if clean in INLINE_TYPES or clean in DOWNLOAD_SUFFIXES else "bin"


def serving(name: str) -> tuple[str, bool]:
    """(media type, inline?) for a stored file: inline only for audio / images / video."""
    suffix = name.rsplit(".", 1)[-1].lower()
    if suffix in INLINE_TYPES:
        return INLINE_TYPES[suffix], True
    return "application/octet-stream", False


class MediaStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def save(self, data: bytes, suffix: str) -> str:
        suffix = safe_suffix(suffix)
        name = f"{secrets.token_urlsafe(12)}.{suffix}"
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_bytes(data)
        return name

    def path(self, name: str) -> Path | None:
        if not SAFE_NAME.match(name):
            return None
        target = self.root / name
        return target if target.is_file() else None

    def remove(self, name: str) -> bool:
        """Delete a stored file; False when the name is not a stored file (nothing to delete)."""
        if (target := self.path(name)) is None:
            return False
        target.unlink(missing_ok=True)
        return True
