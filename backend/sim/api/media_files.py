"""Serving stored media files: one response shape for every route that returns a stored file."""
from __future__ import annotations

from pathlib import Path

from fastapi.responses import FileResponse

from sim.adapters.media import serving

# A stored file is never a page: no script, no plugins, no MIME sniffing, no shared caches.
MEDIA_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "sandbox; default-src 'none'",
    "Cache-Control": "private, max-age=3600",
}


def stored_file_response(path: Path, name: str) -> FileResponse:
    """Audio, images and video inline; everything else as an `application/octet-stream` download."""
    media_type, inline = serving(name)
    return FileResponse(
        path, media_type=media_type,
        content_disposition_type="inline" if inline else "attachment",
        filename=None if inline else name,
        headers=MEDIA_HEADERS,
    )
