"""Private Studio text operations. The media generation eligibility is separate."""
from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import DateTime, ForeignKey, String, Text, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from sim.adapters.db.base import Base
from sim.adapters.db.models import AdminUser, BrokerRequest, Job
from sim.adapters.db.repositories.jobs import JobRepo
from sim.ports.llm import LLMError
from sim.services import jobkinds, preview_operations
from sim.services.llm_audit import private_preview

if TYPE_CHECKING:
    from sim.wiring import Container


class StudioChatOperation(Base):
    __tablename__ = "studio_chat_operation"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("admin_user.id", ondelete="SET NULL"), index=True)
    binding: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id"), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    key_expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    input_cipher: Mapped[str | None] = mapped_column(Text)
    result_cipher: Mapped[str | None] = mapped_column(Text)


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)

    @field_validator("content")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message must not be blank")
        return value


class ChatIn(BaseModel):
    model: str = Field(min_length=1, max_length=120)
    messages: list[Turn] = Field(min_length=1, max_length=24)


class StudioModelUnavailable(LLMError):
    pass


def binding(c: Container, user: AdminUser) -> str | None:
    configured = c.settings.studio_owner_telegram_id
    if not configured or user.telegram_id != configured or not user.is_active or not user.role.has_owner_rights:
        return None
    return hashlib.sha256(f"{user.id}:{configured}".encode()).hexdigest()


def validate(body: ChatIn) -> None:
    if body.messages[-1].role != "user":
        raise ValueError("The final message must be from the user")
    if sum(len(turn.content) for turn in body.messages) > 32000:
        raise ValueError("Conversation exceeds the studio limit")


async def subscription_models(c: Container) -> list[str]:
    rows = await c.llm.list_models()
    return sorted({
        str(row["id"]) for row in rows
        if row.get("available") is True and row.get("tested") is True
        and row.get("billing_type") == "subscription"
        and (str(row.get("id", "")).startswith("claude-sub/claude-sonnet-")
             or str(row.get("id", "")).startswith("codex-sub/gpt-")
             and str(row.get("id", "")).endswith("-sol"))
    })


async def generate(c: Container, body: ChatIn, *, private: bool = False,
                   allowed: Callable[[], Awaitable[bool]] | None = None) -> dict[str, str]:
    models = await subscription_models(c)
    if body.model not in models:
        raise StudioModelUnavailable("Selected subscription model is unavailable or unverified")
    if allowed is not None and not await allowed():
        raise PermissionError("Studio owner binding changed")
    llm = c.audited() if private else c.llm
    result = await llm.chat(
        [{"role": "system", "content": (
            "Ты виртуальный персонаж в личной творческой студии владельца. "
            "Отвечай разговорно на языке пользователя или помогай писать промпты для медиа. "
            "Не утверждай, что видел фото, создал медиа или выполнил действия. "
            "К этому текстовому разговору не приложены изображения или видео."
        )}, *[turn.model_dump() for turn in body.messages]],
        capability="chat:fast", model=body.model, workflow="studio",
    )
    return {"text": result.text, "model": result.model or body.model}


async def start(c: Container, s: AsyncSession, owner: AdminUser, key: str, body: ChatIn) -> StudioChatOperation:
    validate(body)
    now = c.clock()
    expires = preview_operations.key_expiry(key, now)
    fingerprint = preview_operations.digest(preview_operations.canonical(body.model_dump(mode="json")))
    # Same lock order as Studio media. The lock only spans admission, never provider I/O.
    current_owner = await s.scalar(select(AdminUser).where(
        AdminUser.id == owner.id,
    ).with_for_update().execution_options(populate_existing=True))
    scope = binding(c, current_owner) if current_owner is not None else None
    if scope is None:
        raise PermissionError("Personal owner subscription access required")
    existing = await s.get(StudioChatOperation, key)
    if existing is not None:
        if (existing.owner_id, existing.binding, existing.fingerprint) != (owner.id, scope, fingerprint):
            raise preview_operations.PreviewConflict
        return existing
    if not c.settings.studio_chat_queue_enabled:
        raise preview_operations.PreviewDisabled
    if c.clock() >= expires - preview_operations.KEY_TTL + preview_operations.ACCEPT_WINDOW:
        raise ValueError("Studio operation ID is too old")
    unsettled = await s.scalar(select(StudioChatOperation.id).join(Job).where(
        StudioChatOperation.owner_id == owner.id, StudioChatOperation.expires_at > now,
        Job.status.in_(("pending", "running", "failed")),
    ).limit(1))
    if unsettled is not None:
        raise preview_operations.PreviewCapacity("owner")
    job = await JobRepo(s).enqueue(jobkinds.STUDIO_CHAT, {"key": key}, now, max_attempts=1)
    operation = StudioChatOperation(
        id=key, owner_id=owner.id, binding=scope, fingerprint=fingerprint, job_id=job.id,
        input_cipher=c.box.encrypt_json(body.model_dump(mode="json")),
        expires_at=min(now + timedelta(hours=1), expires), key_expires_at=expires,
    )
    s.add(operation)
    await s.flush()
    return operation


async def advance(c: Container, s: AsyncSession, payload: dict) -> None:
    operation = await s.get(StudioChatOperation, payload["key"])
    if operation is None:
        return
    owner = await s.get(AdminUser, operation.owner_id) if operation.owner_id is not None else None
    if (not c.settings.studio_chat_queue_enabled or operation.expires_at <= c.clock()
            or owner is None or binding(c, owner) != operation.binding):
        operation.input_cipher = None
        return

    async def allowed() -> bool:
        current = await s.get(AdminUser, operation.owner_id, populate_existing=True)
        return (c.settings.studio_chat_queue_enabled and operation.expires_at > c.clock()
                and current is not None and binding(c, current) == operation.binding)

    body = ChatIn.model_validate(c.box.decrypt_json(operation.input_cipher))
    token = private_preview.set(True)
    try:
        result = await generate(c, body, private=True, allowed=allowed)
    except (StudioModelUnavailable, PermissionError):
        operation.input_cipher = None
        return
    finally:
        private_preview.reset(token)
    operation.input_cipher = None
    if operation.expires_at > c.clock():
        operation.result_cipher = c.box.encrypt_json(result)


async def purge(s: AsyncSession, now: datetime) -> None:
    expired = select(StudioChatOperation.id).where(
        StudioChatOperation.expires_at <= now,
        StudioChatOperation.input_cipher.is_not(None) | StudioChatOperation.result_cipher.is_not(None),
    ).order_by(StudioChatOperation.expires_at, StudioChatOperation.id).limit(200).with_for_update(
        skip_locked=True,
    )
    await s.execute(update(StudioChatOperation).where(
        StudioChatOperation.id.in_(expired),
    ).values(input_cipher=None, result_cipher=None))
    # Clearing provider receipts before the operation ID expires retains a bounded replay fence.
    finished_jobs = select(Job.id).join(StudioChatOperation).where(
        StudioChatOperation.expires_at <= now, Job.status.in_(("done", "failed", "cancelled")),
    )
    await s.execute(delete(BrokerRequest).where(BrokerRequest.job_id.in_(finished_jobs)))
    await s.execute(delete(StudioChatOperation).where(StudioChatOperation.key_expires_at <= now))
