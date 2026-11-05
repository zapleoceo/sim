"""Shared HTTP helpers for channel transports.

httpx logs every request URL at INFO. Telegram puts the bot token IN the URL path, so that
logger is silenced here: a token in a log line is a leaked token."""
from __future__ import annotations

import logging

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

MEDIA_MAX_BYTES = 60 * 1024 * 1024
MEDIA_TIMEOUT = 90.0
API_TIMEOUT = 30.0


async def download_bounded(
    url: str, *, max_bytes: int = MEDIA_MAX_BYTES, follow_redirects: bool = False
) -> bytes:
    """Stream bytes from a (signed CDN) url, refusing to buffer more than `max_bytes`.

    Deliberately unauthenticated and without redirects: a signed link needs neither, and
    credentials must never ride along to another host."""
    import httpx  # noqa: PLC0415 (lazy: tests run with fakes)

    timeout = httpx.Timeout(MEDIA_TIMEOUT, connect=10.0)
    async with (
        httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects) as c,
        c.stream("GET", url) as r,
    ):
        if r.status_code >= 400:
            raise ValueError(f"media download failed: HTTP {r.status_code}")
        chunks: list[bytes] = []
        total = 0
        async for chunk in r.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"media exceeds {max_bytes} bytes, refusing to buffer")
            chunks.append(chunk)
    return b"".join(chunks)
