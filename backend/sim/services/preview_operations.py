"""One worker execution per test-chat request, with private short-lived results."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.locks import NS_PREVIEW_INPUT, lock
from sim.adapters.db.models import (
    AdminUser,
    BrokerRequest,
    CaptionOperationAlias,
    ContentPost,
    Job,
    Persona,
    PreviewOperation,
    Strategy,
)
from sim.adapters.db.repositories.jobs import JobRepo
from sim.api.schemas import PreviewIn
from sim.services import preview_speech
from sim.services.jobkinds import PREVIEW_REPLY
from sim.services.llm_audit import private_preview
from sim.services.preview_reply import execute_preview

if TYPE_CHECKING:
    from sim.services.conversation import ConversationService
    from sim.wiring import Container

MAX_OWNER_OPEN = 1
MAX_QUEUE_OPEN = 16
MAX_CAPTION_ALIASES = 8
KEY_PATTERN = re.compile(r"([0-9]{13})-([0-9a-f-]{36})")
KEY_TTL = timedelta(hours=24)
RESULT_TTL = timedelta(hours=1)
CLOCK_SKEW = timedelta(minutes=5)
ACCEPT_WINDOW = timedelta(minutes=2)
MAX_INPUT_BYTES = 128 * 1024
VOICE_ONLY = frozenset(("voice_id", "voice_mode", "voice_style", "voice_ambience"))
# Bump when worker preview semantics change incompatibly.
CONTRACT = 1


def key_expiry(key: str, now: datetime) -> datetime:
    match = KEY_PATTERN.fullmatch(key)
    if match is None:
        raise ValueError("Invalid preview request ID")
    timestamp, uuid_text = match.groups()
    if str(UUID(uuid_text, version=4)) != uuid_text:
        raise ValueError("Invalid preview request ID")
    issued = datetime.fromtimestamp(int(timestamp) / 1000, UTC).replace(tzinfo=None)
    expires = issued + KEY_TTL
    if now >= expires or issued > now + CLOCK_SKEW:
        raise ValueError("Preview request ID is outside its acceptance window")
    return expires


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


async def revision(service: ConversationService, persona: Persona, strategy: Strategy,
                   *, with_speech_plan: bool = False) -> str:
    return digest(canonical([
        CONTRACT,
        {col.key: getattr(persona, col.key) for col in Persona.__table__.columns
         if not with_speech_plan or col.key not in VOICE_ONLY},
        {col.key: getattr(strategy, col.key) for col in strategy.__table__.columns},
        asdict(await service.config()),
    ]))


async def scoped(s: AsyncSession, owner: int, resource: int, key: str, *,
                 caption: bool = False) -> PreviewOperation | None:
    identity = PreviewOperation.id == key
    if caption:
        alias = select(CaptionOperationAlias.operation_id).where(
            CaptionOperationAlias.id == key,
        ).scalar_subquery()
        identity = identity | (PreviewOperation.id == alias)
        scope = PreviewOperation.post_id == resource
    else:
        scope = PreviewOperation.post_id.is_(None) & (PreviewOperation.persona_id == resource)
    return await s.scalar(select(PreviewOperation).where(
        identity, PreviewOperation.owner_id == owner, scope,
    ))


async def definitively_missing(s: AsyncSession, owner: int, resource: int, key: str,
                               now: datetime, *, caption: bool = False) -> bool:
    try:
        expires = key_expiry(key, now)
    except ValueError:
        # An expired/invalid key cannot be admitted. A future key is left retryable.
        return KEY_PATTERN.fullmatch(key) is None or int(key[:13]) < int(
            (now - KEY_TTL).replace(tzinfo=UTC).timestamp() * 1000,
        )
    if now < expires - KEY_TTL + ACCEPT_WINDOW:
        return False
    # Wait for any in-flight admission. After this lock, no new operation can use this key.
    await lock(s, NS_PREVIEW_INPUT, 0)
    return await scoped(s, owner, resource, key, caption=caption) is None


async def start(c: Container, s: AsyncSession, owner: int, persona: Persona,
                key: str, body: PreviewIn) -> PreviewOperation:
    with_speech_plan = preview_speech.enabled(c)

    async def snapshot() -> str:
        service = c.conversation(s)
        return await revision(service, persona, await service.strategy_for(persona.id),
                              with_speech_plan=with_speech_plan)

    extra = {"speech_plan": preview_speech.voice_plan(c, persona, body.voice_input)} if with_speech_plan else {}
    return await admit(c, s, owner, persona.id, key, body.model_dump(mode="json"), snapshot, extra=extra)


def caption_draft_revision(post: ContentPost) -> str:
    return digest(canonical({col.key: getattr(post, col.key) for col in ContentPost.__table__.columns}))


async def caption_revision(c: Container, s: AsyncSession, post: ContentPost,
                           request: tuple[str | None, list[dict[str, Any]]] | None = None) -> str:
    if request is None:
        request = await c.content(s).caption_request(post)
    return digest(canonical([
        "caption-v1", caption_draft_revision(post), request,
    ]))


async def start_caption(c: Container, s: AsyncSession, owner: int,
                        post: ContentPost, key: str) -> PreviewOperation:
    async def snapshot() -> str:
        return await caption_revision(c, s, post)

    return await admit(
        c, s, owner, post.persona_id, key, {}, snapshot,
        post_id=post.id, draft_revision=caption_draft_revision(post),
    )


async def admit(c: Container, s: AsyncSession, owner: int, persona: int, key: str, data: dict,
                snapshot: Callable[[], Awaitable[str]], *, post_id: int | None = None,
                draft_revision: str | None = None, extra: dict | None = None) -> PreviewOperation:
    now = c.clock()
    expires = key_expiry(key, now)
    encoded = canonical(data)
    if len(encoded) > MAX_INPUT_BYTES:
        raise ValueError("Preview request is too large")
    fingerprint = digest(encoded)
    await lock(s, NS_PREVIEW_INPUT, 0)
    current = await s.get(PreviewOperation, key)
    if current is None and (alias := await s.get(CaptionOperationAlias, key)) is not None:
        current = await s.get(PreviewOperation, alias.operation_id)
    if current is not None:
        if (current.owner_id, current.post_id, current.fingerprint) != (owner, post_id, fingerprint):
            raise PreviewConflict
        if post_id is None and current.persona_id != persona:
            raise PreviewConflict
        return current
    if c.clock() >= expires - KEY_TTL + ACCEPT_WINDOW:
        raise ValueError("Preview request ID is too old for a new operation")
    if post_id is not None:
        prior = await s.scalar(select(PreviewOperation).where(
            PreviewOperation.owner_id == owner, PreviewOperation.post_id == post_id,
            PreviewOperation.draft_revision == draft_revision,
        ))
        if prior is not None:
            if c.settings.preview_async_enabled:
                aliases = await s.scalar(select(func.count()).select_from(CaptionOperationAlias).where(
                    CaptionOperationAlias.operation_id == prior.id,
                ))
                if aliases < MAX_CAPTION_ALIASES:
                    s.add(CaptionOperationAlias(id=key, operation_id=prior.id))
                    await s.flush()
            return prior
    if not c.settings.preview_async_enabled:
        raise PreviewDisabled
    open_jobs = select(PreviewOperation.id).join(Job, Job.id == PreviewOperation.job_id).where(
        Job.status.in_(("pending", "running")),
    )
    owner_open = await s.scalar(select(func.count()).select_from(
        open_jobs.where(PreviewOperation.owner_id == owner).subquery(),
    ))
    if owner_open >= MAX_OWNER_OPEN:
        raise PreviewCapacity("owner")
    queue_open = await s.scalar(select(func.count()).select_from(open_jobs.subquery()))
    if queue_open >= MAX_QUEUE_OPEN:
        raise PreviewCapacity("queue")
    source_revision = await snapshot()
    job = await JobRepo(s).enqueue(PREVIEW_REPLY, {"key": key}, now, max_attempts=1)
    current = PreviewOperation(
        id=key, owner_id=owner, persona_id=persona, post_id=post_id, draft_revision=draft_revision, job_id=job.id,
        fingerprint=fingerprint, revision=source_revision, expires_at=min(now + RESULT_TTL, expires),
        key_expires_at=expires, input_cipher=c.box.encrypt_json({**data, **(extra or {})}),
    )
    s.add(current)
    await s.flush()
    return current


class PreviewDisabled(Exception):
    pass


class PreviewCapacity(Exception):
    pass


class PreviewConflict(Exception):
    pass


async def run(c: Container, s: AsyncSession, payload: dict) -> dict[str, str]:
    row = await s.get(PreviewOperation, payload["key"])
    if row is None or row.expires_at <= c.clock():
        return {"reason": "expired"}
    user = await s.get(AdminUser, row.owner_id) if row.owner_id is not None else None
    if not c.settings.preview_async_enabled or user is None or not user.is_active:
        row.input_cipher = None
        return {"reason": "unavailable"}
    if row.post_id is not None:
        return await run_caption(c, s, row)
    persona = await s.get(Persona, row.persona_id)
    if persona is None:
        row.input_cipher = None
        return {"reason": "unavailable"}
    service = c.conversation(s)
    strategy = await service.strategy_for(persona.id)
    input_data = c.box.decrypt_json(row.input_cipher)
    plan = input_data.pop("speech_plan", None)
    if row.revision != await revision(service, persona, strategy, with_speech_plan=plan is not None):
        row.input_cipher = None
        return {"reason": "configuration_changed"}
    body = PreviewIn.model_validate(input_data)
    token = private_preview.set(True)
    try:
        result = await execute_preview(persona, body, c, service, text_only=True, strategy=strategy)
    finally:
        private_preview.reset(token)
    if row.expires_at > c.clock() and plan is not None:
        await preview_speech.stage(c, s, row, result["reply"], plan)
    result["recovered_history"] = [turn.model_dump(mode="json") for turn in body.history]
    result["recovered_message"] = body.message
    row.input_cipher = None
    if row.expires_at > c.clock():
        row.result_cipher = c.box.encrypt_json(result)
    return {"reason": "finished"}


async def run_caption(c: Container, s: AsyncSession, row: PreviewOperation) -> dict[str, str]:
    row.input_cipher = None
    post = await s.get(ContentPost, row.post_id)
    if post is None:
        return {"reason": "unavailable"}
    service = c.content(s)
    request = await service.caption_request(post)
    if row.revision != await caption_revision(c, s, post, request):
        return {"reason": "configuration_changed"}
    token = private_preview.set(True)
    try:
        caption = await service.render_caption(request)
    finally:
        private_preview.reset(token)
    if row.expires_at > c.clock():
        row.result_cipher = c.box.encrypt_json({"caption": caption[:2200]})
    return {"reason": "finished"}


async def purge(s: AsyncSession, now: datetime, *, enabled: bool = True) -> None:
    await preview_speech.expire(s, now)
    pending = select(Job.id).join(PreviewOperation, Job.id == PreviewOperation.job_id).where(
        Job.status == "pending",
    )
    if enabled:
        pending = pending.where(PreviewOperation.expires_at <= now)
    expired_pending = pending.limit(200).with_for_update(of=Job, skip_locked=True)
    await s.execute(update(Job).where(Job.id.in_(expired_pending), Job.status == "pending").values(
        status="cancelled", finished_at=now, last_error=None if enabled else "preview_disabled",
    ))
    eligible = select(PreviewOperation.id).join(Job, Job.id == PreviewOperation.job_id).where(
        (PreviewOperation.expires_at <= now) | (Job.last_error == "preview_disabled"),
        Job.status.in_(("pending", "done", "failed", "cancelled")),
        (PreviewOperation.input_cipher.is_not(None) | PreviewOperation.result_cipher.is_not(None)),
    ).limit(200)
    await s.execute(update(PreviewOperation).where(
        PreviewOperation.id.in_(eligible),
        (PreviewOperation.input_cipher.is_not(None) | PreviewOperation.result_cipher.is_not(None)),
    ).values(input_cipher=None, result_cipher=None))
    receipt_jobs = select(BrokerRequest.job_id).join(
        PreviewOperation, PreviewOperation.job_id == BrokerRequest.job_id,
    ).join(Job, Job.id == BrokerRequest.job_id).where(
        PreviewOperation.expires_at <= now, Job.status.in_(("done", "failed", "cancelled")),
    ).limit(200)
    await s.execute(delete(BrokerRequest).where(BrokerRequest.job_id.in_(receipt_jobs)))
    dead = select(PreviewOperation.id).join(Job, Job.id == PreviewOperation.job_id).where(
        PreviewOperation.key_expires_at <= now, Job.status.in_(("done", "failed", "cancelled")),
        ~select(BrokerRequest.job_id).where(BrokerRequest.job_id == Job.id).exists(),
    ).limit(200)
    await s.execute(delete(PreviewOperation).where(PreviewOperation.id.in_(dead)))
