"""Reading a file from a multipart form (operator send, media library upload)."""
from __future__ import annotations

import mimetypes
from typing import Any

from litestar.datastructures import UploadFile
from litestar.exceptions import ClientException

GENERIC_MIME = "application/octet-stream"


def upload_mime(upload: UploadFile) -> str:
    """The browser's content type, or a guess from the file name when it sent a generic one."""
    mime = (upload.content_type or "").split(";", 1)[0].strip().lower()
    if mime and mime != GENERIC_MIME:
        return mime
    guessed, _ = mimetypes.guess_type(upload.filename or "")
    return guessed or GENERIC_MIME


async def read_upload(form: Any, field: str = "file") -> tuple[UploadFile, bytes]:
    """The uploaded file of `field` and its bytes; 400 when it is missing or empty."""
    upload = form.get(field)
    if not isinstance(upload, UploadFile):
        raise ClientException(detail=f"Нужен файл в поле «{field}»")
    data = await upload.read()
    if not data:
        raise ClientException(detail="Файл пустой")
    return upload, data
