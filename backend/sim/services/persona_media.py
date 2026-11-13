"""Persona media gallery: photos, videos and video notes the persona can send.

An upload is accepted only when its declared type is allowed and the file signature (detected by
`filetype`) is that same type, so a renamed page or script is never stored as a picture. The file
on disk lives exactly as long as its row: deleting the media deletes both."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import filetype
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import PersonaMedia
from sim.adapters.db.repositories.persona_media import PersonaMediaRepo
from sim.adapters.media import MediaStore
from sim.clock import Clock, utcnow
from sim.domain.enums import MediaKind

MAX_MEDIA_BYTES = 50 * 1024 * 1024
CAPTION_LIMIT = 500
SUFFIX_BY_TYPE = {
    "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif",
    "video/mp4": "mp4", "video/webm": "webm",
}


class MediaRejected(Exception):
    """The upload is not acceptable (kind, type or content)."""


class MediaTooLarge(MediaRejected):
    """The upload exceeds MAX_MEDIA_BYTES."""


class MediaNotFound(Exception):
    """No such media for this persona, or its file is gone."""


def parse_kind(raw: str) -> MediaKind:
    try:
        return MediaKind(raw)
    except ValueError:
        raise MediaRejected(f"kind must be one of: {', '.join(k.value for k in MediaKind)}") from None


def parse_tags(raw: str) -> list[str]:
    return [tag.strip() for tag in raw.split(",") if tag.strip()]


def checked_suffix(data: bytes, content_type: str) -> str:
    """Storage suffix of an allowed upload whose bytes really are the declared type."""
    declared = content_type.split(";", 1)[0].strip().lower()
    if declared not in SUFFIX_BY_TYPE:
        raise MediaRejected(f"File type '{declared}' is not allowed")
    if len(data) > MAX_MEDIA_BYTES:
        raise MediaTooLarge(f"File too large (max {MAX_MEDIA_BYTES // 1024 // 1024} MB)")
    detected = filetype.guess(data)
    if detected is None or detected.mime != declared:
        raise MediaRejected("File content does not match its type")
    return SUFFIX_BY_TYPE[declared]


class PersonaMediaService:
    def __init__(self, session: AsyncSession, store: MediaStore, *, clock: Clock = utcnow) -> None:
        self.repo = PersonaMediaRepo(session)
        self.store = store
        self.clock = clock

    async def upload(
        self, persona_id: int, *, kind: str, data: bytes, content_type: str,
        caption: str = "", tags: str = "",
    ) -> PersonaMedia:
        media_kind = parse_kind(kind)
        name = await self.store.save(data, checked_suffix(data, content_type))
        row = PersonaMedia(
            persona_id=persona_id, kind=media_kind, storage_name=name,
            caption=caption[:CAPTION_LIMIT], tags=parse_tags(tags), created_at=self.clock(),
        )
        try:
            return await self.repo.add(row)
        except Exception:
            self.store.remove(name)   # no row, no file
            raise

    async def list(self, persona_id: int, kind: str | None = None) -> Sequence[PersonaMedia]:
        return await self.repo.for_persona(persona_id, kind=parse_kind(kind) if kind else None)

    async def get(self, persona_id: int, media_id: int) -> PersonaMedia:
        row = await self.repo.get(media_id)
        if row is None or row.persona_id != persona_id:
            raise MediaNotFound("Media not found")
        return row

    async def delete(self, persona_id: int, media_id: int) -> None:
        row = await self.get(persona_id, media_id)
        await self.repo.delete(row)
        self.store.remove(row.storage_name)

    async def file(self, persona_id: int, media_id: int) -> tuple[Path, str]:
        """(path on disk, storage name) of the media file."""
        row = await self.get(persona_id, media_id)
        if (path := self.store.path(row.storage_name)) is None:
            raise MediaNotFound("File not found on disk")
        return path, row.storage_name
