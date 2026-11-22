"""`/api/personas/{id}/media` (a persona's media library: list, upload) and `/api/media/{id}`
(edit, delete, authenticated file streaming: a media id is global)."""
from __future__ import annotations

from typing import Any

from litestar import Request, Response, delete, get, patch, post
from litestar.exceptions import ClientException, NotFoundException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api import views
from sim.api.context import ApiContext
from sim.api.files import file_response
from sim.api.queries import get_or_404, persona_or_404
from sim.api.schemas import MediaPatchIn, sent_fields
from sim.api.uploads import read_upload, upload_mime
from sim.db.models import Chat, MediaAsset, Message
from sim.domain.enums import MediaKind, MediaSource
from sim.services.media_library import (
    NotAReference,
    add_media,
    delete_media,
    scenes_of,
    update_media,
)

Json = dict[str, Any]
LIBRARY_LIMIT = 1000
LIBRARY_MIME_PREFIXES = ("image/", "video/")
TRUE_VALUES = frozenset({"1", "true", "on", "yes"})


def split_tags(value: str) -> list[str]:
    return [tag.strip() for tag in value.split(",") if tag.strip()]


@get("/personas/{persona_id:int}/media")
async def list_media(persona_id: int, db: AsyncSession, source: MediaSource | None = None,
                     kind: MediaKind | None = None) -> Json:
    """This persona's library and generated media by default; client attachments (they have
    no persona) of her chats only when asked for."""
    await persona_or_404(db, persona_id)
    stmt = select(MediaAsset)
    if source == MediaSource.INCOMING:
        her_chats = select(Message.media_id).join(Chat, Chat.id == Message.chat_id).where(
            Chat.persona_id == persona_id, Message.media_id.is_not(None))
        stmt = stmt.where(MediaAsset.source == source, MediaAsset.id.in_(her_chats))
    else:
        sources = (source,) if source is not None else (MediaSource.LIBRARY,
                                                         MediaSource.GENERATED)
        stmt = stmt.where(MediaAsset.persona_id == persona_id, MediaAsset.source.in_(sources))
    if kind is not None:
        stmt = stmt.where(MediaAsset.kind == kind)
    rows = list(await db.scalars(stmt.order_by(MediaAsset.id.desc()).limit(LIBRARY_LIMIT)))
    scenes = await scenes_of(db, rows)
    return {"items": [views.library_media_view(m, scenes.get(m.id)) for m in rows]}


@post("/personas/{persona_id:int}/media", status_code=201)
async def upload_media(persona_id: int, request: Request[Any, Any, Any], ctx: ApiContext,
                       db: AsyncSession) -> Json:
    """Multipart `file`, `caption`, `tags` (comma-separated), `is_reference`. Photos and
    videos only: the library is what the persona sends and generates from."""
    persona = await persona_or_404(db, persona_id)
    form = await request.form()
    upload, data = await read_upload(form)
    mime = upload_mime(upload)
    if not mime.startswith(LIBRARY_MIME_PREFIXES):
        raise ClientException(detail="В медиатеку можно загрузить только фото или видео")
    media = await add_media(
        db, ctx.deps.storage, ctx.deps.tools, persona_id=persona.id, data=data, mime=mime,
        file_name=upload.filename or "", source=MediaSource.LIBRARY,
        caption=str(form.get("caption") or ""), tags=split_tags(str(form.get("tags") or "")),
        is_reference=str(form.get("is_reference") or "").strip().lower() in TRUE_VALUES)
    return views.library_media_view(media, None)


@patch("/media/{media_id:int}")
async def edit_media(media_id: int, data: MediaPatchIn, db: AsyncSession) -> Json:
    """Caption, tags, `is_reference` (a photo: her face for every generation) and
    `is_room_ref` (a generated photo of a room of her apartment: that room's reference)."""
    await get_or_404(db, MediaAsset, media_id, "Файл")
    try:
        media = await update_media(db, media_id, **sent_fields(data))
    except NotAReference as exc:
        raise ClientException(detail=str(exc)) from exc
    scenes = await scenes_of(db, [media])
    return views.library_media_view(media, scenes.get(media.id))


@delete("/media/{media_id:int}")
async def remove_media(media_id: int, ctx: ApiContext, db: AsyncSession) -> None:
    await get_or_404(db, MediaAsset, media_id, "Файл")
    await delete_media(db, ctx.deps.storage, media_id)


@get("/media/{media_id:int}/file")
async def media_file(media_id: int, request: Request[Any, Any, Any], ctx: ApiContext,
                     db: AsyncSession) -> Response[bytes]:
    media = await get_or_404(db, MediaAsset, media_id, "Файл")
    try:
        data = await ctx.deps.storage.get(media.storage_key)
    except (FileNotFoundError, KeyError) as exc:
        raise NotFoundException(detail=f"Файл {media_id} пропал из хранилища") from exc
    return file_response(data, mime=media.mime, file_name=media.file_name,
                         range_header=request.headers.get("range"))


ROUTES = [list_media, upload_media, edit_media, remove_media, media_file]
