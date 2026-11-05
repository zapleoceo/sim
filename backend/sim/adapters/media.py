"""Local media store (downloaded voice / images) and the downloader.

Files are stored under settings.media_dir with random names and served only through the
authenticated /api/media/{name} route; the original URL is not kept (a channel file URL may
embed a bot token)."""
from __future__ import annotations

import re
import secrets
from pathlib import Path

import httpx

MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
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


async def download(url: str) -> bytes:
    """Fetch a media URL with a size cap."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > MAX_DOWNLOAD_BYTES:
                    raise ValueError("media too large")
                chunks.append(chunk)
    return b"".join(chunks)
