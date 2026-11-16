"""Allowlisted content operations. No sends, model calls, publication or account access."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import (
    Client,
    ContentPost,
    ContentPublishIntent,
    Dialog,
    LlmCall,
    McpAudit,
    McpGrant,
    Message,
)
from sim.clock import Clock
from sim.domain.enums import ContentStatus
from sim.services.auth import hash_token


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DialogRead(Input):
    dialog_id: int = Field(gt=0)
    after_id: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)


class PostRead(Input):
    post_id: int = Field(gt=0)


class LeadEdit(Input):
    dialog_id: int = Field(gt=0)
    field: Literal["notes", "next_step"]
    value: str = Field(max_length=8000)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=500)


class PostEdit(PostRead):
    caption: str = Field(max_length=8000)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=500)


TOOLS = {
    "read_dialog": ("dialog:read", DialogRead, "Read a scoped dialog page and lead context; no mark-read."),
    "read_checks": ("trace:read", DialogRead, "Read scoped LLM metadata, never raw prompts or secrets."),
    "read_post": ("content:read", PostRead, "Read one scoped content post and revision."),
    "edit_lead_note": ("lead:edit", LeadEdit, "Edit only notes or next_step after explicit user request."),
    "edit_draft_caption": ("content:edit", PostEdit, "Edit one draft caption after explicit user request; never publish."),
}
SCOPES = frozenset(t[0] for t in TOOLS.values())


class McpError(Exception):
    pass


def revision(value: dict) -> str:
    return hash_token(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str))


def post_data(post: ContentPost) -> dict:
    value = {"id": post.id, "caption": post.caption, "status": post.status.value}
    return {**value, "revision": revision(value)}


def lead_data(client: Client) -> dict:
    value = {"notes": client.notes, "next_step": client.next_step}
    return {**value, "revision": revision(value)}


class ContentMcp:
    def __init__(self, s: AsyncSession, grant: McpGrant, user_id: int, clock: Clock):
        self.s, self.grant, self.user_id, self.clock = s, grant, user_id, clock

    def audit(self, action: str, outcome: str, *, entity_id: int | None = None,
              reason: str = "", before: str | None = None, after: str | None = None) -> None:
        self.s.add(McpAudit(grant_id=self.grant.id, user_id=self.user_id, action=action,
            entity_id=entity_id, outcome=outcome, reason_hash=hash_token(reason) if reason else None,
            before_hash=before, after_hash=after, created_at=self.clock()))

    async def call(self, name: str, arguments: dict) -> dict:
        if name not in TOOLS:
            raise McpError("Unknown tool")
        scope, schema, _ = TOOLS[name]
        if scope not in self.grant.scopes:
            raise McpError("Scope denied")
        args = schema.model_validate(arguments)
        if hasattr(args, "reason") and not args.reason.strip():
            raise McpError("A change reason is required")
        is_dialog = hasattr(args, "dialog_id")
        entity_id = args.dialog_id if is_dialog else args.post_id
        allowed = self.grant.dialog_ids if is_dialog else self.grant.post_ids
        if entity_id not in allowed:
            raise McpError("Entity denied")
        if is_dialog:
            dialog = await self.s.get(Dialog, entity_id)
            if dialog is None:
                raise McpError("Entity not found")
            client = await self.s.scalar(select(Client).where(Client.id == dialog.client_id).with_for_update())
            if name == "read_dialog":
                rows = list(await self.s.scalars(select(Message).where(Message.dialog_id == entity_id,
                    Message.id > args.after_id).order_by(Message.id).limit(args.limit)))
                result = {"dialog_id": entity_id, "lead": lead_data(client), "passport": client.passport,
                    "messages": [{"id": m.id, "text": m.text, "author": m.author.value,
                        "created_at": m.created_at.isoformat() + "Z", "delivery": m.delivery.value,
                        "guard_verdict": m.guard_verdict, "guard_violations": m.guard_violations,
                        "handoff_reason": m.handoff_reason} for m in rows],
                    "next_after_id": rows[-1].id if rows else args.after_id}
            elif name == "read_checks":
                rows = list(await self.s.scalars(select(LlmCall).where(LlmCall.dialog_id == entity_id,
                    LlmCall.id > args.after_id).order_by(LlmCall.id).limit(args.limit)))
                result = {"items": [{"id": r.id, "workflow": r.workflow, "model": r.model,
                    "created_at": r.created_at.isoformat() + "Z", "latency_ms": r.latency_ms,
                    "failed": bool(r.error)} for r in rows],
                    "next_after_id": rows[-1].id if rows else args.after_id}
            else:
                before = lead_data(client)["revision"]
                if before != args.expected_revision:
                    raise McpError("Revision conflict; read again")
                setattr(client, args.field, args.value)
                result = lead_data(client)
                self.audit(name, "edited", entity_id=entity_id, reason=args.reason,
                           before=before, after=result["revision"])
                return result
        else:
            post = await self.s.scalar(select(ContentPost).where(ContentPost.id == entity_id).with_for_update())
            if post is None:
                raise McpError("Entity not found")
            if name == "edit_draft_caption":
                if post.status is not ContentStatus.DRAFT or await self.s.get(ContentPublishIntent, post.id):
                    raise McpError("Only unpublished drafts may be edited")
                before = post_data(post)["revision"]
                if before != args.expected_revision:
                    raise McpError("Revision conflict; read again")
                post.caption = args.caption
                result = post_data(post)
                self.audit(name, "edited", entity_id=entity_id, reason=args.reason,
                           before=before, after=result["revision"])
                return result
            result = post_data(post)
        if len(json.dumps(result, ensure_ascii=False).encode()) > 256_000:
            raise McpError("Response too large; request a smaller page")
        self.audit(name, "read", entity_id=entity_id)
        return result
