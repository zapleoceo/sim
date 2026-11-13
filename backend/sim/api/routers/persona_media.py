"""Persona media gallery: upload, list, download and delete photos / videos / video notes.
The rules live in `services/persona_media.py`; its errors become 404/413/422 in api/errors.py."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from fastapi.responses import FileResponse

from sim.api.deps import App, Db, Owner, authed
from sim.api.media_files import stored_file_response
from sim.api.routers.catalog import persona_or_404
from sim.api.schemas import persona_media_out
from sim.services.persona_media import MAX_MEDIA_BYTES

router = APIRouter(
    prefix="/api/personas/{persona_id}/media",
    tags=["persona-media"],
    dependencies=[Depends(authed)],
)


@router.post("", status_code=201)
async def upload_media(
    persona_id: int, s: Db, c: App, _: Owner,
    file: Annotated[UploadFile, File()],
    kind: Annotated[str, Form()],
    caption: Annotated[str, Form()] = "",
    tags: Annotated[str, Form()] = "",
):
    await persona_or_404(s, persona_id)
    data = await file.read(MAX_MEDIA_BYTES + 1)
    await file.close()
    row = await c.persona_media(s).upload(
        persona_id, kind=kind, data=data, content_type=file.content_type or "",
        caption=caption, tags=tags,
    )
    return persona_media_out(row)


@router.get("")
async def list_media(persona_id: int, s: Db, c: App, kind: str | None = None):
    await persona_or_404(s, persona_id)
    return {"items": [persona_media_out(m) for m in await c.persona_media(s).list(persona_id, kind)]}


@router.delete("/{media_id}", status_code=204)
async def delete_media(persona_id: int, media_id: int, s: Db, c: App, _: Owner):
    await persona_or_404(s, persona_id)
    await c.persona_media(s).delete(persona_id, media_id)
    return Response(status_code=204)


@router.get("/{media_id}/file")
async def download_media(persona_id: int, media_id: int, s: Db, c: App) -> FileResponse:
    await persona_or_404(s, persona_id)
    path, name = await c.persona_media(s).file(persona_id, media_id)
    return stored_file_response(path, name)
