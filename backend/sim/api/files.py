"""Serving a stored media file to a logged-in admin: correct content type, a single byte range
(browsers seek in video with `Range`), and headers that keep a stored file from acting as a
page. Images, video and audio are shown inline; everything else is a download."""
from __future__ import annotations

from urllib.parse import quote

from litestar import Response

INLINE_PREFIXES = ("image/", "video/", "audio/")
DOWNLOAD_MIME = "application/octet-stream"
MEDIA_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "sandbox; default-src 'none'",
    "Cache-Control": "private, max-age=3600",
    "Accept-Ranges": "bytes",
}


class RangeNotSatisfiable(Exception):
    pass


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """`bytes=start-end` / `bytes=start-` / `bytes=-suffix` -> inclusive (start, end).
    None = serve the whole file (no header, several ranges or a malformed one)."""
    if not header or not header.startswith("bytes=") or "," in header:
        return None
    start_text, sep, end_text = header.removeprefix("bytes=").strip().partition("-")
    if not sep:
        return None
    try:
        if not start_text:
            length = int(end_text)
            if length <= 0 or size == 0:
                raise RangeNotSatisfiable
            return max(size - length, 0), size - 1
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        raise RangeNotSatisfiable
    return start, min(end, size - 1)


def content_disposition(mime: str, file_name: str) -> tuple[str, str]:
    """(served media type, Content-Disposition)."""
    if mime.startswith(INLINE_PREFIXES):
        return mime, "inline"
    name = file_name or "file"
    return DOWNLOAD_MIME, f"attachment; filename*=UTF-8''{quote(name)}"


def file_response(data: bytes, *, mime: str, file_name: str,
                  range_header: str | None) -> Response[bytes]:
    media_type, disposition = content_disposition(mime, file_name)
    headers = {**MEDIA_HEADERS, "Content-Disposition": disposition}
    size = len(data)
    try:
        byte_range = parse_range(range_header, size)
    except RangeNotSatisfiable:
        return Response(b"", status_code=416, media_type=media_type,
                        headers={**headers, "Content-Range": f"bytes */{size}"})
    if byte_range is None:
        return Response(data, media_type=media_type, headers=headers)
    start, end = byte_range
    return Response(data[start:end + 1], status_code=206, media_type=media_type,
                    headers={**headers, "Content-Range": f"bytes {start}-{end}/{size}"})
