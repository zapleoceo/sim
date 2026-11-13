"""Persona media gallery: upload, list, download and delete photos / videos / video notes."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse

from sim.adapters.db.models import PersonaMedia
from sim.adapters.db.repositories.catalog import PersonaRepo
from sim.adapters.db.repositories.persona_media import PersonaMediaRepo
from sim.adapters.media import serving
from sim.api.deps import App, Db, Owner, authed
from sim.api.schemas import persona_media_out
from sim.clock import utcnow
from sim.domain.enums import MediaKind

router = APIRouter(
    prefix="/api/personas/{persona_id}/media",
    tags=["persona-media"],
    dependencies=[Depends(authed)],
)

ALLOWED_MIME = {
    "image/jpeg", "image/png", "image/webp", "image/gif",
    "video/mp4", "video/webm",
}
MAX_MEDIA_BYTES = 50 * 1024 * 1024  # 50 MB

MEDIA_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, max-age=3600",
}


async def _persona_or_404(s: Db, persona_id: int):
    p = await PersonaRepo(s).get(persona_id)
    if p is None:
        raise HTTPException(404, "Persona not found")
    return p


def _suffix_from_mime(mime: str) -> str:
    return {
        "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif",
        "video/mp4": "mp4", "video/webm": "webm",
    }.get(mime, "bin")


def _parse_tags(raw: str) -> list[str]:
    return [t.strip() for t in raw.split(",") if t.strip()] if raw else []


@router.post("", status_code=201)
async def upload_media(
    persona_id: int,
    s: Db,
    c: App,
    _: Owner,
    file: Annotated[UploadFile, File()],
    kind: Annotated[str, Form()],
    caption: Annotated[str, Form()] = "",
    tags: Annotated[str, Form()] = "",
):
    await _persona_or_404(s, persona_id)
    try:
        media_kind = MediaKind(kind)
    except ValueError:
        raise HTTPException(422, f"kind must be one of: {', '.join(v.value for v in MediaKind)}") from None
    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in ALLOWED_MIME:
        raise HTTPException(422, f"File type '{mime}' is not allowed")
    data = await file.read(MAX_MEDIA_BYTES + 1)
    if len(data) > MAX_MEDIA_BYTES:
        raise HTTPException(413, f"File too large (max {MAX_MEDIA_BYTES // 1024 // 1024} MB)")
    suffix = _suffix_from_mime(mime)
    storage_name = c.media.save(data, suffix)
    row = PersonaMedia(
        persona_id=persona_id,
        kind=media_kind,
        storage_name=storage_name,
        caption=caption[:500],
        tags=_parse_tags(tags),
        created_at=utcnow(),
    )
    repo = PersonaMediaRepo(s)
    await repo.add(row)
    return persona_media_out(row)


@router.get("")
async def list_media(
    persona_id: int,
    s: Db,
    kind: str | None = None,
):
    await _persona_or_404(s, persona_id)
    mk = None
    if kind:
        try:
            mk = MediaKind(kind)
        except ValueError:
            raise HTTPException(422, f"kind must be one of: {', '.join(v.value for v in MediaKind)}") from None
    items = await PersonaMediaRepo(s).for_persona(persona_id, kind=mk)
    return {"items": [persona_media_out(m) for m in items]}


@router.delete("/{media_id}", status_code=204)
async def delete_media(persona_id: int, media_id: int, s: Db, _: Owner):
    await _persona_or_404(s, persona_id)
    repo = PersonaMediaRepo(s)
    row = await repo.get(media_id)
    if row is None or row.persona_id != persona_id:
        raise HTTPException(404, "Media not found")
    await repo.delete(row)
    return Response(status_code=204)


@router.get("/{media_id}/file")
async def download_media(persona_id: int, media_id: int, s: Db, c: App):
    await _persona_or_404(s, persona_id)
    row = await PersonaMediaRepo(s).get(media_id)
    if row is None or row.persona_id != persona_id:
        raise HTTPException(404, "Media not found")
    path = c.media.path(row.storage_name)
    if path is None:
        raise HTTPException(404, "File not found on disk")
    media_type, inline = serving(row.storage_name)
    return FileResponse(
        path, media_type=media_type,
        content_disposition_type="inline" if inline else "attachment",
        filename=None if inline else row.storage_name,
        headers=MEDIA_HEADERS,
    )
