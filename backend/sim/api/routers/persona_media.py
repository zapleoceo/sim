"""Persona media gallery: upload, list, download and delete photos / videos / video notes.
The rules live in `services/persona_media.py`; this router maps them to HTTP."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse

from sim.api.deps import App, Db, Owner, authed
from sim.api.media_files import stored_file_response
from sim.api.routers.catalog import persona_or_404
from sim.api.schemas import persona_media_out
from sim.services.persona_media import (
    MAX_MEDIA_BYTES,
    MediaNotFound,
    MediaRejected,
    MediaTooLarge,
)

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
    try:
        row = await c.persona_media(s).upload(
            persona_id, kind=kind, data=data, content_type=file.content_type or "",
            caption=caption, tags=tags,
        )
    except MediaTooLarge as exc:
        raise HTTPException(413, str(exc)) from None
    except MediaRejected as exc:
        raise HTTPException(422, str(exc)) from None
    return persona_media_out(row)


@router.get("")
async def list_media(persona_id: int, s: Db, c: App, kind: str | None = None):
    await persona_or_404(s, persona_id)
    try:
        items = await c.persona_media(s).list(persona_id, kind)
    except MediaRejected as exc:
        raise HTTPException(422, str(exc)) from None
    return {"items": [persona_media_out(m) for m in items]}


@router.delete("/{media_id}", status_code=204)
async def delete_media(persona_id: int, media_id: int, s: Db, c: App, _: Owner):
    await persona_or_404(s, persona_id)
    try:
        await c.persona_media(s).delete(persona_id, media_id)
    except MediaNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    return Response(status_code=204)


@router.get("/{media_id}/file")
async def download_media(persona_id: int, media_id: int, s: Db, c: App) -> FileResponse:
    await persona_or_404(s, persona_id)
    try:
        path, name = await c.persona_media(s).file(persona_id, media_id)
    except MediaNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    return stored_file_response(path, name)
