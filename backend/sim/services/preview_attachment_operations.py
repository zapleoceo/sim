"""Private preview uploads with local text parsing and one-shot Broker submission."""
from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.locks import NS_PREVIEW_INPUT, lock
from sim.adapters.db.models import AdminUser, BrokerRequest, Job, Persona, PreviewAttachmentOperation
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.session import transaction
from sim.ports.llm import LLMError
from sim.services import preview_operations, preview_speech
from sim.services.job_execution import require_live_execution
from sim.services.jobkinds import PREVIEW_ATTACHMENT
from sim.services.llm_audit import private_preview
from sim.services.preview_media import MAX_BYTES, TEXT_LIMIT, AttachmentError, classify, message_text

if TYPE_CHECKING:
    from sim.wiring import Container

AUDIO = frozenset(("audio", "voice"))
TERMINAL = frozenset(("done", "unknown", "error", "cancelled"))
MAX_RETAINED = 8
PREVIEW_MESSAGE_LIMIT = 3800
# Base64 grows by 4/3. Leave headroom for prompt text and the JSON envelope.
MAX_VISION_BYTES = 1400 * 1024


def enabled(c: Container) -> bool:
    return (c.settings.preview_attachment_queue_enabled and c.settings.preview_async_enabled
            and c.preview_asr is not None and preview_speech.enabled(c))


async def scoped(s: AsyncSession, owner: int, persona: int, key: str) -> PreviewAttachmentOperation | None:
    return await s.scalar(select(PreviewAttachmentOperation).where(
        PreviewAttachmentOperation.id == key, PreviewAttachmentOperation.owner_id == owner,
        PreviewAttachmentOperation.persona_id == persona,
    ))


async def definitively_missing(s: AsyncSession, owner: int, persona: int, key: str, now: datetime) -> bool:
    try:
        expires = preview_operations.key_expiry(key, now)
    except ValueError:
        return not preview_operations.KEY_PATTERN.fullmatch(key) or int(key[:13]) < int(
            (now - preview_operations.KEY_TTL).replace(tzinfo=UTC).timestamp() * 1000,
        )
    if now < expires - preview_operations.KEY_TTL + preview_operations.ACCEPT_WINDOW:
        return False
    await lock(s, NS_PREVIEW_INPUT, 0)
    return await scoped(s, owner, persona, key) is None


async def start(c: Container, s: AsyncSession, owner: int, persona: int, key: str,
                data: bytes, filename: str, content_type: str, voice: bool) -> PreviewAttachmentOperation:
    now = c.clock()
    expires = preview_operations.key_expiry(key, now)
    if not data or len(data) > MAX_BYTES:
        raise AttachmentError("Пустой файл или размер больше 10 МБ")
    name = PurePath(filename.replace(chr(92), "/")).name[:120] or "file"
    mime = content_type.split(";", 1)[0].strip().lower()[:100]
    kind = classify(name, mime, voice=voice)
    if kind == "image" and len(data) > MAX_VISION_BYTES:
        raise AttachmentError("Изображение больше 1,4 МиБ. Уменьшите файл и попробуйте снова.")
    digest = preview_operations.digest(preview_operations.canonical(
        [name, mime, kind, hashlib.sha256(data).hexdigest()],
    ))
    await lock(s, NS_PREVIEW_INPUT, 0)
    row = await s.get(PreviewAttachmentOperation, key)
    if row is not None:
        if (row.owner_id, row.persona_id, row.fingerprint) != (owner, persona, digest):
            raise preview_operations.PreviewConflict
        return row
    if c.clock() >= expires - preview_operations.KEY_TTL + preview_operations.ACCEPT_WINDOW:
        raise ValueError("Attachment request ID is too old")
    if not enabled(c):
        raise preview_operations.PreviewDisabled
    retained = select(PreviewAttachmentOperation.id).where(PreviewAttachmentOperation.expires_at > now)
    owner_count = await s.scalar(select(func.count()).select_from(
        retained.where(PreviewAttachmentOperation.owner_id == owner).subquery(),
    ))
    if owner_count >= 4:
        raise preview_operations.PreviewCapacity("owner")
    if await s.scalar(select(func.count()).select_from(retained.subquery())) >= MAX_RETAINED:
        raise preview_operations.PreviewCapacity("queue")
    row = PreviewAttachmentOperation(
        id=key, owner_id=owner, persona_id=persona, fingerprint=digest, kind=kind, status="ready",
        deadline_at=min(now + timedelta(minutes=3), expires),
        expires_at=min(now + timedelta(hours=1), expires), key_expires_at=expires,
        input_cipher=c.box.encrypt_json({"name": name, "mime": mime, "file": base64.b64encode(data).decode()}),
    )
    s.add(row)
    if kind == "file":
        complete(c, row, name, data.decode("utf-8", "replace").strip()[:TEXT_LIMIT] or "[пустой файл]")
    else:
        await JobRepo(s).enqueue(PREVIEW_ATTACHMENT, {"key": key}, now)
    await s.flush()
    return row


def complete(c: Container, row: PreviewAttachmentOperation, name: str, text: str) -> None:
    rendered = message_text(row.kind, name, text[:TEXT_LIMIT])
    if len(rendered) > PREVIEW_MESSAGE_LIMIT:
        rendered = rendered[:PREVIEW_MESSAGE_LIMIT - 1] + "…"
    row.result_cipher = c.box.encrypt_json({"kind": row.kind, "name": name, "text": rendered})
    row.status = "done"


async def reserve(c: Container, key: str) -> bool:
    await require_live_execution()
    async with transaction(c.factory) as s:
        claim = await s.scalar(update(PreviewAttachmentOperation).where(
            PreviewAttachmentOperation.id == key, PreviewAttachmentOperation.status == "ready",
        ).values(status="lookup").returning(PreviewAttachmentOperation.id))
        return claim is not None


async def advance(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    row = await s.get(PreviewAttachmentOperation, payload["key"])
    if row is None or row.status in TERMINAL:
        return
    user = await s.get(AdminUser, row.owner_id) if row.owner_id is not None else None
    if user is None or not user.is_active or await s.get(Persona, row.persona_id) is None:
        row.status = "cancelled"
        row.input_cipher = None
        return
    if row.deadline_at <= c.clock():
        row.status = "unknown"
        row.input_cipher = None
        return
    port = c.preview_asr
    if (row.status == "ready" and not enabled(c)) or (row.kind in AUDIO and port is None):
        row.status = "cancelled"
        row.input_cipher = None
        return
    saved = c.box.decrypt_json(row.input_cipher)
    key = f"preview-asr-{row.id}"
    first = row.status == "ready" and await reserve(c, row.id)
    token = private_preview.set(True)
    try:
        if row.kind == "image":
            if not first:
                row.status = "unknown"
                return
            data = base64.b64decode(saved["file"], validate=True)
            text = await c.preview_attachments().describe(data, saved["mime"])
            complete(c, row, saved["name"], text)
        elif port is not None:
            if first:
                receipt = await port.submit(key, base64.b64decode(saved["file"], validate=True), saved["name"])
            elif row.broker_id is None:
                receipt = await port.by_key(key)
            else:
                receipt = await port.status(row.broker_id)
            if receipt is not None:
                if not receipt.job_id or len(receipt.job_id) > 128 or row.broker_id not in (None, receipt.job_id):
                    row.status = "unknown"
                    return
                row.broker_id = receipt.job_id
                if receipt.status == "done" and receipt.text is not None:
                    complete(c, row, saved["name"], receipt.text.strip() or "[не удалось распознать речь]")
                elif receipt.status in TERMINAL:
                    row.status = "error" if receipt.status == "done" else receipt.status
    except LLMError:
        if row.kind == "image":
            row.status = "unknown"
    finally:
        private_preview.reset(token)

    if row.status not in TERMINAL:
        row.status = "poll" if row.broker_id else "lookup"
        await JobRepo(s).enqueue(PREVIEW_ATTACHMENT, {"key": row.id},
                                  min(c.clock() + timedelta(seconds=3), row.deadline_at))


async def purge(s: AsyncSession, now: datetime) -> None:
    expired_jobs = select(Job.id).join(
        PreviewAttachmentOperation, PreviewAttachmentOperation.id == Job.payload["key"].as_string(),
    ).where(Job.kind == PREVIEW_ATTACHMENT, PreviewAttachmentOperation.expires_at <= now,
            Job.status.in_(("done", "failed", "cancelled")))
    await s.execute(delete(BrokerRequest).where(BrokerRequest.job_id.in_(expired_jobs)))
    await s.execute(update(PreviewAttachmentOperation).where(
        PreviewAttachmentOperation.expires_at <= now,
        PreviewAttachmentOperation.input_cipher.is_not(None) | PreviewAttachmentOperation.result_cipher.is_not(None),
    ).values(status="cancelled", input_cipher=None, result_cipher=None))
    await s.execute(delete(PreviewAttachmentOperation).where(PreviewAttachmentOperation.key_expires_at <= now))
