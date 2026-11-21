"""«Клонировать» (docs/personas.md): a new persona made from an existing one, copying only the
parts the owner ticked (`ClonePart`). One copier per part: a new part is a new enum value and
one entry in `COPIERS`, the others do not change.

Never copied: channels, clients and chats, planned days, generated media, generations, reply
runs. The clone has no channel, so it answers nobody until a Telegram account is connected.

`clone_persona` runs inside the caller's transaction. Library files are copied to NEW storage
keys (the clone may delete its media without touching the source); if anything fails, the
files copied so far are deleted and the error goes up, so the caller's rollback leaves nothing."""
from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import MediaAsset, Persona, PersonaArc, PersonaCanon
from sim.domain.enums import CanonStatus, ClonePart, MediaKind, MediaSource
from sim.ports import MediaStoragePort, PortError

log = logging.getLogger(__name__)

CLONE_MEDIA_KINDS = (MediaKind.PHOTO, MediaKind.VIDEO)


class PersonaNameTaken(ValueError):
    """Another persona already has this name (`persona.name` is unique)."""


class SourceNotFound(LookupError):
    pass


@dataclass
class CloneJob:
    """What every copier gets: the session, the storage, both personas and the storage keys
    copied so far (deleted again if the clone fails)."""

    session: AsyncSession
    storage: MediaStoragePort
    source: Persona
    target: Persona
    new_keys: list[str] = field(default_factory=list)


class Copier(Protocol):
    async def copy(self, job: CloneJob) -> None: ...


@dataclass(frozen=True)
class FieldsCopier:
    """Plain columns of `persona`."""

    fields: tuple[str, ...]

    async def copy(self, job: CloneJob) -> None:
        for name in self.fields:
            value = getattr(job.source, name)
            setattr(job.target, name, deepcopy(value))


class ArcsCopier:
    """Story arcs with their stage and status."""

    async def copy(self, job: CloneJob) -> None:
        arcs = await job.session.scalars(
            select(PersonaArc).where(PersonaArc.persona_id == job.source.id)
            .order_by(PersonaArc.id))
        job.session.add_all([
            PersonaArc(persona_id=job.target.id, title=arc.title, summary=arc.summary,
                       stage=arc.stage, status=arc.status, started_on=arc.started_on,
                       ends_on=arc.ends_on)
            for arc in arcs])


class CanonCopier:
    """Active facts she invented about herself; the link to the source chat is not kept."""

    async def copy(self, job: CloneJob) -> None:
        facts = await job.session.scalars(
            select(PersonaCanon).where(PersonaCanon.persona_id == job.source.id,
                                       PersonaCanon.status == CanonStatus.ACTIVE)
            .order_by(PersonaCanon.id))
        job.session.add_all([
            PersonaCanon(persona_id=job.target.id, fact=item.fact, chat_id=None, event_id=None,
                         status=CanonStatus.ACTIVE)
            for item in facts])


class MediaCopier:
    """Library photos and videos with caption, tags and the reference flag. Every file is
    copied to a new storage key; the avatar follows its copy."""

    async def copy(self, job: CloneJob) -> None:
        assets = list(await job.session.scalars(
            select(MediaAsset).where(MediaAsset.persona_id == job.source.id,
                                     MediaAsset.source == MediaSource.LIBRARY,
                                     MediaAsset.kind.in_(CLONE_MEDIA_KINDS))
            .order_by(MediaAsset.id)))
        for asset in assets:
            data = await job.storage.get(asset.storage_key)
            key = await job.storage.put(data, mime=asset.mime, prefix=MediaSource.LIBRARY.value)
            job.new_keys.append(key)
            clone = MediaAsset(
                persona_id=job.target.id, source=MediaSource.LIBRARY, kind=asset.kind,
                is_reference=asset.is_reference, storage_key=key, mime=asset.mime,
                file_name=asset.file_name, size_bytes=asset.size_bytes, sha256=asset.sha256,
                width=asset.width, height=asset.height, duration_s=asset.duration_s,
                caption=asset.caption, tags=list(asset.tags or []),
                description=asset.description, understanding=asset.understanding)
            job.session.add(clone)
            await job.session.flush()
            if job.source.avatar_media_id == asset.id:
                job.target.avatar_media_id = clone.id


COPIERS: dict[ClonePart, Copier] = {
    ClonePart.BASICS: FieldsCopier(("gender", "language", "timezone", "birth_date")),
    ClonePart.PROMPT: FieldsCopier(("prompt",)),
    ClonePart.AGENTS: FieldsCopier(("agents", "attention")),
    ClonePart.VOICE: FieldsCopier(("voice_id", "voice_style", "voice_ambience_enabled")),
    ClonePart.GUARD: FieldsCopier(("guard_rules",)),
    ClonePart.APPEARANCE: FieldsCopier(("appearance",)),
    ClonePart.WORLD: FieldsCopier(("life",)),
    ClonePart.ARCS: ArcsCopier(),
    ClonePart.MEDIA: MediaCopier(),
    ClonePart.SWITCHES: FieldsCopier((
        "ai_enabled", "voice_enabled", "photo_enabled", "video_enabled", "calls_enabled",
        "reply_delay_s")),
    ClonePart.CANON: CanonCopier(),
}
# What the dialog ticks by default: everything except the canon (her chats' inventions).
DEFAULT_PARTS: tuple[ClonePart, ...] = tuple(p for p in ClonePart if p != ClonePart.CANON)


async def clone_persona(session: AsyncSession, storage: MediaStoragePort, source_id: int,
                        name: str, parts: Iterable[ClonePart]) -> Persona:
    """A new active persona named `name` with the chosen parts of `source_id` (flushed).
    The parts are applied in the order of `ClonePart`, whatever order they came in."""
    clean = " ".join(name.split())
    source = await session.get(Persona, source_id)
    if source is None:
        raise SourceNotFound(f"persona {source_id} not found")
    if not clean:
        raise ValueError("the new persona needs a name")
    if await session.scalar(select(Persona.id).where(Persona.name == clean)) is not None:
        raise PersonaNameTaken(clean)
    target = Persona(name=clean, is_active=True)
    session.add(target)
    await session.flush()
    job = CloneJob(session=session, storage=storage, source=source, target=target)
    wanted = set(parts)
    try:
        for part in ClonePart:
            if part in wanted:
                await COPIERS[part].copy(job)
        await session.flush()
    except BaseException:
        await drop_files(storage, job.new_keys)
        raise
    log.info("persona %s cloned from %s: %s", target.id, source.id,
             ",".join(p.value for p in ClonePart if p in wanted))
    return target


async def drop_files(storage: MediaStoragePort, keys: Iterable[str]) -> None:
    for key in keys:
        with contextlib.suppress(PortError, KeyError, FileNotFoundError):
            await storage.delete(key)
