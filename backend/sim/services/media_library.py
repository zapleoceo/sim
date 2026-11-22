"""Every file of SIM goes through here: the persona's library, generated media and client
attachments share one table (`media_asset`). The bytes live in MediaStoragePort, the row keeps
the key, the probe result, the caption/tags and what a model understood from the file."""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Generation, MediaAsset, Message
from sim.domain.enums import (
    Direction,
    MediaKind,
    MediaSource,
    MessageStatus,
    UnderstandingStatus,
)
from sim.ports import MediaStoragePort, MediaToolsPort, PortError

log = logging.getLogger(__name__)

# What the reply model may send from the catalog.
CATALOG_KINDS = (MediaKind.PHOTO, MediaKind.VIDEO)
CATALOG_SOURCES = (MediaSource.LIBRARY, MediaSource.GENERATED)
CATALOG_LIMIT = 200
# `generation.scene` key: the owner pinned this photo as the reference of its room.
ROOM_REF = "room_ref"
# Outgoing messages that count as "already sent to this chat" (pending ones are on the way).
DELIVERED_OR_PENDING = (MessageStatus.SENT, MessageStatus.PENDING)


class MediaNotFound(LookupError):
    pass


class NotAReference(ValueError):
    """The file can not be this kind of reference (a video as a face, a street as a room)."""


def kind_from_mime(mime: str) -> MediaKind:
    major = mime.split("/", 1)[0].lower()
    if major == "image":
        return MediaKind.PHOTO
    if major == "video":
        return MediaKind.VIDEO
    if major == "audio":
        return MediaKind.AUDIO
    return MediaKind.FILE


def normalize_tags(tags: Sequence[str]) -> list[str]:
    """Lowercase, trimmed, without empties and repeats, original order kept."""
    out: list[str] = []
    for tag in tags:
        clean = " ".join(str(tag).split()).lower().strip("#")
        if clean and clean not in out:
            out.append(clean)
    return out


async def add_media(
    session: AsyncSession,
    storage: MediaStoragePort,
    tools: MediaToolsPort,
    *,
    persona_id: int | None,
    data: bytes,
    mime: str,
    file_name: str,
    source: MediaSource,
    kind: MediaKind | None = None,
    caption: str = "",
    tags: Sequence[str] = (),
    is_reference: bool = False,
    description: str = "",
    understanding: UnderstandingStatus = UnderstandingStatus.SKIPPED,
    generation_id: int | None = None,
) -> MediaAsset:
    """Store the bytes and create the row (flushed, so it has an id)."""
    probe = await tools.probe(data, mime)
    real_mime = probe.mime or mime or "application/octet-stream"
    key = await storage.put(data, mime=real_mime, prefix=source.value)
    asset = MediaAsset(
        persona_id=persona_id, source=source, kind=kind or kind_from_mime(real_mime),
        is_reference=is_reference, storage_key=key, mime=real_mime, file_name=file_name,
        size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), width=probe.width,
        height=probe.height, duration_s=probe.duration_s, caption=caption.strip(),
        tags=normalize_tags(tags), description=description, understanding=understanding,
        generation_id=generation_id,
    )
    session.add(asset)
    await session.flush()
    return asset


async def get_media(session: AsyncSession, media_id: int) -> MediaAsset:
    asset = await session.get(MediaAsset, media_id)
    if asset is None:
        raise MediaNotFound(f"media {media_id} not found")
    return asset


async def update_media(
    session: AsyncSession,
    media_id: int,
    *,
    caption: str | None = None,
    tags: Sequence[str] | None = None,
    is_reference: bool | None = None,
    is_room_ref: bool | None = None,
) -> MediaAsset:
    asset = await get_media(session, media_id)
    if is_reference and asset.kind != MediaKind.PHOTO:
        raise NotAReference("Референсом внешности может быть только фото")
    if is_room_ref is not None:                  # checked before anything else changes
        await pin_room_photo(session, asset, pinned=is_room_ref)
    if caption is not None:
        asset.caption = caption.strip()
    if tags is not None:
        asset.tags = normalize_tags(tags)
    if is_reference is not None:
        asset.is_reference = is_reference
    await session.flush()
    return asset


async def pin_room_photo(session: AsyncSession, asset: MediaAsset, *, pinned: bool) -> None:
    """The owner's reference photo of a room of her apartment (docs/persona-life.md, «Облик и
    дом»): `generation.scene.room_ref`, sent first among the room's references
    (sim/services/generation.room_ids). Only a generated photo taken in one of her rooms."""
    generation = (await session.get(Generation, asset.generation_id)
                  if asset.generation_id is not None else None)
    scene = dict(generation.scene or {}) if generation is not None else {}
    if generation is None or asset.kind != MediaKind.PHOTO or not scene.get("room"):
        raise NotAReference(
            "Эталоном комнаты может быть только сгенерированное фото комнаты её квартиры")
    if pinned:
        scene[ROOM_REF] = True
    else:
        scene.pop(ROOM_REF, None)
    generation.scene = scene


async def scenes_of(session: AsyncSession,
                    assets: Sequence[MediaAsset]) -> dict[int, dict[str, Any]]:
    """media id -> the scene its generation was made in (generated media only)."""
    by_generation = {a.generation_id: a.id for a in assets if a.generation_id is not None}
    if not by_generation:
        return {}
    rows = await session.execute(select(Generation.id, Generation.scene)
                                 .where(Generation.id.in_(by_generation)))
    found = {generation_id: scene or {} for generation_id, scene in rows.tuples()}
    return {a.id: found[a.generation_id] for a in assets
            if a.generation_id is not None and a.generation_id in found}


async def delete_media(session: AsyncSession, storage: MediaStoragePort, media_id: int) -> None:
    """Delete the row, then the file. Messages keep their text (media_id becomes NULL)."""
    asset = await get_media(session, media_id)
    key = asset.storage_key
    await session.delete(asset)
    await session.flush()
    try:
        await storage.delete(key)
    except PortError as exc:
        log.warning("media %s: file %s not deleted: %s", media_id, key, exc)


async def catalog_for_chat(
    session: AsyncSession, persona_id: int, chat_id: int,
) -> tuple[list[MediaAsset], list[tuple[MediaAsset, datetime]]]:
    """(photos/videos the persona may still send to this chat, already sent ones with time).

    "Sent" = an outgoing message of this chat with this media that is sent or still pending;
    a failed delivery does not count, so the media is offered again."""
    sent_at = func.max(func.coalesce(Message.sent_at, Message.created_at))
    sent_rows = (await session.execute(
        select(MediaAsset, sent_at)
        .join(Message, Message.media_id == MediaAsset.id)
        .where(Message.chat_id == chat_id, Message.direction == Direction.OUT,
               Message.status.in_(DELIVERED_OR_PENDING),
               MediaAsset.kind.in_(CATALOG_KINDS))
        .group_by(MediaAsset.id)
        .order_by(sent_at, MediaAsset.id)
    )).all()
    sent = [(asset, at) for asset, at in sent_rows]
    sent_ids = [asset.id for asset, _ in sent]
    query = (
        select(MediaAsset)
        .where(MediaAsset.persona_id == persona_id, MediaAsset.source.in_(CATALOG_SOURCES),
               MediaAsset.kind.in_(CATALOG_KINDS))
        .order_by(MediaAsset.id.desc())
        .limit(CATALOG_LIMIT)
    )
    if sent_ids:
        query = query.where(MediaAsset.id.not_in(sent_ids))
    available = list((await session.execute(query)).scalars())
    available.sort(key=lambda asset: asset.id)
    return available, sent
