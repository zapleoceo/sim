"""Dormant Studio media orchestration. No production activation switch yet.

An intent is committed before touching a provider. Any uncertain result blocks ALL
new media intents for that owner; neither another UUID nor restart is a retry permit.
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from sim.adapters.crypto import SecretBox
from sim.adapters.db.base import Base
from sim.adapters.db.models import AdminUser
from sim.adapters.db.session import transaction
from sim.adapters.studio_broker import StudioBroker
from sim.clock import utcnow
from sim.ports.speech import SpeechPort
from sim.ports.video import VideoPort
from sim.services.studio_files import valid_artifact


class StudioOperation(Base):
    __tablename__ = "studio_operation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("admin_user.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    result_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)


class MediaRequest(BaseModel):
    operation_id: UUID
    kind: Literal["speech", "video"]
    text: str = Field(min_length=1, max_length=2000)


class StudioUnavailable(Exception):
    pass


class StudioConflict(Exception):
    pass


class StudioMedia:
    def __init__(self, factory: async_sessionmaker[AsyncSession], box: SecretBox,
                 speech: SpeechPort | None, video: VideoPort | None, *, enabled: bool = False,
                 remote: StudioBroker | None = None):
        self.factory, self.box = factory, box
        self.speech, self.video, self.enabled = speech, video, enabled
        self.remote = remote

    async def recent(self, owner_id: int) -> list[StudioOperation]:
        async with self.factory() as session:
            return list(await session.scalars(select(StudioOperation).where(
                StudioOperation.owner_id == owner_id).order_by(
                StudioOperation.status.desc(), StudioOperation.created_at.desc(),
                StudioOperation.id.desc()).limit(50)))

    async def get(self, owner_id: int, operation_id: str) -> StudioOperation | None:
        async with self.factory() as session:
            row = await session.get(StudioOperation, operation_id)
            return row if row is not None and row.owner_id == owner_id else None

    async def execute(self, owner_id: int, request: MediaRequest) -> StudioOperation:
        # Production wiring ALWAYS leaves this false. Tests inject fake ports explicitly.
        if not self.enabled:
            raise StudioUnavailable("Studio generation is disabled; capabilities not verified")
        if not request.text.strip():
            raise StudioConflict("Empty request")
        if (request.kind == "speech" and self.speech is None
                or request.kind == "video" and self.video is None and self.remote is None):
            raise StudioUnavailable("Provider port unavailable")
        operation_id = str(request.operation_id)
        fingerprint = hashlib.sha256(json.dumps(
            {"kind": request.kind, "text": request.text}, sort_keys=True,
        ).encode()).hexdigest()
        async with transaction(self.factory) as session:
            # PostgreSQL serializes different request IDs for the same owner too.
            owner = await session.scalar(select(AdminUser).where(
                AdminUser.id == owner_id).with_for_update())
            if owner is None:
                raise StudioConflict("Owner missing")
            existing = await session.get(StudioOperation, operation_id)
            if existing is not None:
                if existing.owner_id != owner_id or existing.fingerprint != fingerprint:
                    raise StudioConflict("Operation ID conflict")
                return existing
            unresolved = await session.scalar(select(StudioOperation.id).where(
                StudioOperation.owner_id == owner_id, StudioOperation.status == "unknown"))
            if unresolved:
                raise StudioConflict("Previous outcome unknown; automatic retry prohibited")
            row = StudioOperation(id=operation_id, owner_id=owner_id, fingerprint=fingerprint,
                                  kind=request.kind, status="unknown")
            session.add(row)
        # No catch-and-retry: cancellation, process loss, provider errors and failed result
        # persistence all leave a durable unknown intent. No raw error/prompt is stored.
        if request.kind == "speech":
            assert self.speech is not None
            result = await self.speech.synthesize(request.text, voice="Kore", workflow="studio")
            data, mime = result.audio, result.mime_type
        elif self.remote is not None:
            remote_id = await self.remote.submit_video(request.text)
            # Persist accepted identity before polling. If this commit is uncertain,
            # leave the original intent blocked; never POST again.
            async with transaction(self.factory) as session:
                saved = await session.get(StudioOperation, operation_id, with_for_update=True)
                assert saved is not None
                saved.result_encrypted = self.box.encrypt_json({
                    "remote_id": remote_id, "scope": self.remote.scope})
            return await self.reconcile(owner_id, operation_id)
        else:
            assert self.video is not None
            result = await self.video.generate_video(
                request.text, duration_s=4, resolution="720p", workflow="studio")
            data, mime = result.video, result.mime_type
        if not valid_artifact(data, mime, request.kind):
            raise StudioConflict("Invalid provider artifact; outcome requires review")
        return await self.complete(operation_id, data, mime)

    async def complete(self, operation_id: str, data: bytes, mime: str) -> StudioOperation:
        encrypted = self.box.encrypt_json({"data": base64.b64encode(data).decode(), "mime": mime})
        async with transaction(self.factory) as session:
            saved = await session.get(StudioOperation, operation_id, with_for_update=True)
            assert saved is not None
            if saved.status != "complete":
                saved.result_encrypted, saved.status = encrypted, "complete"
        return saved

    async def reconcile(self, owner_id: int, operation_id: str) -> StudioOperation:
        if not self.enabled:
            raise StudioUnavailable("Studio reconciliation disabled")
        row = await self.get(owner_id, operation_id)
        if row is None:
            raise StudioConflict("Operation missing")
        if row.status == "complete":
            return row
        receipt = self.box.decrypt_json(row.result_encrypted)
        if (row.kind != "video" or self.remote is None or not receipt.get("remote_id")
                or receipt.get("scope") != self.remote.scope):
            raise StudioConflict("No matching retained remote receipt; resubmission prohibited")
        result = await self.remote.read_video(receipt["remote_id"])
        if result is None:
            return row
        if not valid_artifact(result.video, result.mime_type, "video"):
            raise StudioConflict("Remote artifact invalid; outcome remains blocked")
        return await self.complete(operation_id, result.video, result.mime_type)

    def artifact(self, row: StudioOperation) -> tuple[bytes, str]:
        if row.status != "complete":
            raise StudioConflict("No confirmed artifact")
        result = self.box.decrypt_json(row.result_encrypted)
        data = base64.b64decode(result["data"], validate=True)
        if not valid_artifact(data, result["mime"], row.kind):
            raise StudioConflict("Stored artifact invalid")
        return data, result["mime"]
