#!/usr/bin/python3
"""One-off v1 -> v2 transfer, step 2: write the export of scripts/export_v1.py into the v2 DB.

Usage (in the api container, docs/deploy.md):
    python scripts/import_v1.py <export.json> <dir with the v1 media files>

Settings come from the environment like every SIM process: SIM_DATABASE_URL, SIM_SECRET_KEY (the
SAME key as v1: the Telegram session is decrypted with it and re-encrypted in the v2 format) and
SIM_MEDIA_DIR. Idempotent: running it again updates the same rows and skips media already
imported (same sha256). Models per purpose are left empty: the owner picks them on the persona
page from the Broker catalog."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.storage import LocalMediaStorage
from sim.config import get_settings
from sim.crypto import decrypt_json, encrypt_json
from sim.db.models import AdminUser, Channel, MediaAsset, Persona
from sim.db.session import make_engine, make_sessionmaker, transaction
from sim.domain.enums import (
    ChannelKind,
    ChannelStatus,
    MediaKind,
    MediaSource,
    UnderstandingStatus,
)
from sim.ports import MediaStoragePort

FORMAT = "sim-v1-export"
VERSION = 1
LIBRARY_PREFIX = "library"
# v1 stored media under random names like `Xy3_aB9k-Qw2zR1t.jpg` (sim.adapters.media.SAFE_NAME).
V1_MEDIA_NAME = re.compile(r"^[A-Za-z0-9_-]{8,64}\.[A-Za-z0-9]{1,8}$")
V1_MEDIA_KINDS = {"photo": MediaKind.PHOTO, "video": MediaKind.VIDEO,
                  "video_note": MediaKind.VIDEO}
MIME_BY_SUFFIX = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp",
    "gif": "image/gif", "mp4": "video/mp4", "webm": "video/webm",
}
V1_CHANNEL_STATUS = {"active": ChannelStatus.CONNECTED, "error": ChannelStatus.ERROR,
                     "expired": ChannelStatus.ERROR}


class ExportFormatError(ValueError):
    """The export file cannot be imported (wrong format, missing data)."""


@dataclass(frozen=True)
class MediaItem:
    storage_name: str
    kind: MediaKind
    mime: str
    caption: str
    tags: list[str]
    is_reference: bool


@dataclass
class ImportReport:
    persona_id: int = 0
    persona_created: bool = False
    admins: int = 0
    channel: str = "skipped"
    media_added: int = 0
    media_skipped: int = 0
    media_missing: list[str] = field(default_factory=list)


# --- pure mapping ----------------------------------------------------------------------------


def load_export(raw: str) -> dict[str, Any]:
    document = json.loads(raw)
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise ExportFormatError("not a SIM v1 export (format)")
    if document.get("version") != VERSION:
        raise ExportFormatError(f"unsupported export version {document.get('version')!r}")
    if not isinstance(document.get("persona"), dict) or not document["persona"].get("name"):
        raise ExportFormatError("the export has no persona")
    return document


def persona_fields(document: dict[str, Any]) -> dict[str, Any]:
    """v2 persona columns from the export. Empty v1 values keep v2 defaults."""
    v1 = document["persona"]
    fields: dict[str, Any] = {
        "name": v1["name"],
        "prompt": v1.get("prompt") or "",
        "voice_id": v1.get("voice_id") or "",
        "voice_style": v1.get("voice_style") or "",
        "gender": v1.get("gender") or "female",
        "language": v1.get("language") or "ru",
    }
    if v1.get("timezone"):
        fields["timezone"] = v1["timezone"]
    if v1.get("birth_date"):
        fields["birth_date"] = date.fromisoformat(v1["birth_date"])
    if document.get("guard_prompt"):
        fields["guard_rules"] = document["guard_prompt"]
    return fields


def channel_status(v1_status: str) -> ChannelStatus:
    """v1 session status -> v2 channel status (an unfinished v1 login is a new channel)."""
    return V1_CHANNEL_STATUS.get(v1_status, ChannelStatus.NEW)


def v2_credentials(v1_session: dict[str, Any]) -> dict[str, Any]:
    """v1 `session_enc` content -> v2 `credentials_enc` content
    (`{"api_id", "api_hash", "session", "proxy"}`, read by sim.telegram.client)."""
    try:
        api_id = int(v1_session.get("api_id") or 0)
    except (TypeError, ValueError):
        api_id = 0
    api_hash = str(v1_session.get("api_hash") or "").strip()
    session = str(v1_session.get("string_session") or "").strip()
    if api_id <= 0 or not api_hash or not session:
        raise ExportFormatError("the v1 Telegram session is not logged in "
                                "(needs api_id, api_hash, string_session)")
    return {"api_id": api_id, "api_hash": api_hash, "session": session,
            "proxy": str(v1_session.get("proxy") or "").strip()}


def media_items(document: dict[str, Any]) -> list[MediaItem]:
    """Library media from the export; photos become appearance references."""
    items = []
    for row in document.get("media") or []:
        name = str(row.get("storage_name") or "")
        kind = V1_MEDIA_KINDS.get(str(row.get("kind")))
        if kind is None or not V1_MEDIA_NAME.match(name):
            raise ExportFormatError(f"unexpected v1 media row: {row!r}")
        suffix = name.rsplit(".", 1)[-1].lower()
        mime = MIME_BY_SUFFIX.get(suffix, "application/octet-stream")
        items.append(MediaItem(
            storage_name=name, kind=kind, mime=mime, caption=str(row.get("caption") or ""),
            tags=[str(tag) for tag in row.get("tags") or []],
            is_reference=kind is MediaKind.PHOTO,
        ))
    return items


def read_media(media_dir: Path, name: str) -> bytes | None:
    """Bytes of a v1 media file, None when it is missing. Blocking: call in a thread."""
    if not V1_MEDIA_NAME.match(name):
        return None
    path = media_dir / name
    return path.read_bytes() if path.is_file() else None


# --- database --------------------------------------------------------------------------------


async def upsert_persona(session: AsyncSession, fields: dict[str, Any]) -> tuple[Persona, bool]:
    """The persona with this name; else the only persona (the one sim.seed made); else new."""
    persona = await session.scalar(select(Persona).where(Persona.name == fields["name"]))
    if persona is None:
        existing = (await session.scalars(select(Persona).order_by(Persona.id))).all()
        persona = existing[0] if len(existing) == 1 else None
    created = persona is None
    if persona is None:
        persona = Persona(name=fields["name"])
        session.add(persona)
    for key, value in fields.items():
        setattr(persona, key, value)
    await session.flush()
    return persona, created


async def upsert_admins(session: AsyncSession, admins: list[dict[str, Any]]) -> int:
    for row in admins:
        telegram_id = int(row["telegram_id"])
        admin = await session.scalar(select(AdminUser).where(AdminUser.telegram_id == telegram_id))
        if admin is None:
            admin = AdminUser(telegram_id=telegram_id)
            session.add(admin)
        admin.name = admin.name or str(row.get("name") or "")
        admin.username = admin.username or row.get("username") or None
        admin.is_active = True
    await session.flush()
    return len(admins)


async def upsert_channel(session: AsyncSession, persona_id: int, v1: dict[str, Any] | None,
                         secret_keys: str) -> str:
    if not v1 or not v1.get("session_enc"):
        return "skipped: no v1 Telegram session"
    credentials = v2_credentials(decrypt_json(v1["session_enc"], secret_keys))
    channel = await session.scalar(
        select(Channel).where(Channel.persona_id == persona_id,
                              Channel.kind == ChannelKind.TELEGRAM_USER).order_by(Channel.id))
    created = channel is None
    if channel is None:
        channel = Channel(persona_id=persona_id, kind=ChannelKind.TELEGRAM_USER)
        session.add(channel)
    channel.title = str(v1.get("name") or "")[:120]
    channel.account_label = str(v1.get("account_label") or "")[:120]
    channel.status = channel_status(str(v1.get("status") or ""))
    channel.credentials_enc = encrypt_json(credentials, secret_keys)
    channel.last_error = None
    await session.flush()
    return "created" if created else "updated"


async def import_media(session: AsyncSession, persona_id: int, items: list[MediaItem],
                       media_dir: Path, storage: MediaStoragePort, report: ImportReport) -> None:
    for item in items:
        data = await asyncio.to_thread(read_media, media_dir, item.storage_name)
        if data is None:
            report.media_missing.append(item.storage_name)
            continue
        digest = hashlib.sha256(data).hexdigest()
        duplicate = await session.scalar(select(MediaAsset.id).where(
            MediaAsset.persona_id == persona_id, MediaAsset.source == MediaSource.LIBRARY,
            MediaAsset.sha256 == digest))
        if duplicate is not None:
            report.media_skipped += 1
            continue
        key = await storage.put(data, mime=item.mime, prefix=LIBRARY_PREFIX)
        try:
            session.add(MediaAsset(
                persona_id=persona_id, source=MediaSource.LIBRARY, kind=item.kind,
                is_reference=item.is_reference, storage_key=key, mime=item.mime,
                file_name=item.storage_name, size_bytes=len(data), sha256=digest,
                caption=item.caption, tags=item.tags, understanding=UnderstandingStatus.SKIPPED,
            ))
            await session.flush()
        except Exception:
            await storage.delete(key)   # no row, no file
            raise
        report.media_added += 1


async def import_export(session: AsyncSession, document: dict[str, Any], *, media_dir: Path,
                        storage: MediaStoragePort, secret_keys: str) -> ImportReport:
    """Everything in the caller's transaction; files are written before their rows."""
    report = ImportReport()
    items = media_items(document)
    persona, report.persona_created = await upsert_persona(session, persona_fields(document))
    report.persona_id = persona.id
    report.admins = await upsert_admins(session, list(document.get("admins") or []))
    report.channel = await upsert_channel(session, persona.id, document.get("channel"),
                                          secret_keys)
    await import_media(session, persona.id, items, media_dir, storage, report)
    return report


async def run(document: dict[str, Any], media_dir: Path) -> ImportReport:
    settings = get_settings()
    engine = make_engine(settings.database_url, pool_size=1)
    try:
        async with transaction(make_sessionmaker(engine)) as session:
            return await import_export(
                session, document, media_dir=media_dir,
                storage=LocalMediaStorage(settings.media_dir), secret_keys=settings.secret_key)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import a SIM v1 export into the v2 database.")
    parser.add_argument("export", type=Path, help="JSON written by scripts/export_v1.py")
    parser.add_argument("media_dir", type=Path, help="directory with the v1 media files")
    args = parser.parse_args(argv)
    document = load_export(args.export.read_text(encoding="utf-8"))
    report = asyncio.run(run(document, args.media_dir))
    print(f"persona #{report.persona_id} ({'created' if report.persona_created else 'updated'}), "
          f"admins: {report.admins}, channel: {report.channel}, media added: "
          f"{report.media_added}, already there: {report.media_skipped}")
    if report.media_missing:
        print(f"media files not found in {args.media_dir}: {', '.join(report.media_missing)}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
