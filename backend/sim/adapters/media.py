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


class MediaStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def save(self, data: bytes, suffix: str) -> str:
        suffix = re.sub(r"[^A-Za-z0-9]", "", suffix)[:8] or "bin"
        name = f"{secrets.token_urlsafe(12)}.{suffix}"
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_bytes(data)
        return name

    def path(self, name: str) -> Path | None:
        if not SAFE_NAME.match(name):
            return None
        target = self.root / name
        return target if target.is_file() else None
