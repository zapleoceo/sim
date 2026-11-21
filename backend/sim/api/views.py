"""Response shapes of docs/api.md, built from ORM rows. One function per shape (DRY): every
route that returns a `ChatRow`, `Media`, `TimelineItem`… goes through the same builder."""
from __future__ import annotations

from typing import Any

from sim.api.schemas import PERSONA_FIELDS
from sim.db.models import (
    AdminUser,
    Call,
    Channel,
    Chat,
    ChatEvent,
    Client,
    Generation,
    LlmCall,
    MediaAsset,
    Message,
    Persona,
    ReplyRun,
)
from sim.domain.enums import EventKind

Json = dict[str, Any]


def media_url(media_id: int) -> str:
    return f"/api/media/{media_id}/file"


def admin_view(admin: AdminUser) -> Json:
    return {"id": admin.id, "name": admin.name, "username": admin.username}


def media_view(media: MediaAsset) -> Json:
    return {
        "id": media.id, "kind": media.kind, "mime": media.mime, "url": media_url(media.id),
        "caption": media.caption, "tags": list(media.tags or []),
        "description": media.description, "source": media.source,
        "is_reference": media.is_reference, "width": media.width, "height": media.height,
        "duration_s": media.duration_s, "file_name": media.file_name,
    }


def message_view(message: Message, media: MediaAsset | None) -> Json:
    view: Json = {
        "id": message.id, "direction": message.direction, "author": message.author,
        "kind": message.kind, "text": message.text, "status": message.status,
        "error": message.error, "sent_at": message.sent_at,
    }
    if media is not None:
        view["media"] = media_view(media)
    return view


def call_view(call: Call) -> Json:
    return {"id": call.id, "direction": call.direction, "status": call.status,
            "duration_s": call.duration_s, "transcript": list(call.transcript or [])}


def generation_view(generation: Generation) -> Json:
    return {"id": generation.id, "kind": generation.kind, "status": generation.status,
            "caption": generation.caption, "error": generation.error}


def timeline_item(event: ChatEvent, *, message: Message | None = None,
                  media: MediaAsset | None = None, call: Call | None = None,
                  generation: Generation | None = None) -> Json:
    item: Json = {
        "id": event.id, "kind": event.kind, "actor": event.actor,
        "created_at": event.created_at, "in_context": event.in_context,
        "reply_run_id": event.reply_run_id, "data": dict(event.data or {}),
    }
    if message is not None:
        item["message"] = message_view(message, media)
    if call is not None:
        item["call"] = call_view(call)
    if generation is not None:
        item["generation"] = generation_view(generation)
    return item


def pending_message_item(message: Message, media: MediaAsset | None, actor: str) -> Json:
    """An outgoing message the outbox has not handed to Telegram yet, so it has no journal
    event (`id` is null); the `message_out` event replaces it in the timeline once sent."""
    return {
        "id": None, "kind": EventKind.MESSAGE_OUT, "actor": actor,
        "created_at": message.created_at, "in_context": True,
        "reply_run_id": message.reply_run_id, "data": {},
        "message": message_view(message, media),
    }


def client_brief(client: Client) -> Json:
    return {"id": client.id, "name": client.name, "username": client.username,
            "ai_enabled": client.ai_enabled}


def client_view(client: Client) -> Json:
    return {
        "id": client.id, "name": client.name, "username": client.username,
        "phone": client.phone, "timezone": client.timezone, "notes": client.notes,
        "ai_enabled": client.ai_enabled, "memory": list(client.memory or []),
        "created_at": client.created_at, "last_seen_at": client.last_seen_at,
    }


def chat_row(chat: Chat, client: Client, last: Message | None) -> Json:
    return {
        "id": chat.id, "client": client_brief(client), "ai_enabled": chat.ai_enabled,
        "needs_operator": chat.needs_operator, "unread_count": chat.unread_count,
        "last_message_at": chat.last_message_at,
        "last_message": None if last is None else {
            "kind": last.kind, "direction": last.direction, "author": last.author,
            "text": last.text,
        },
    }


def llm_call_view(call: LlmCall) -> Json:
    return {
        "id": call.id, "purpose": call.purpose, "model": call.model, "request": call.request,
        "response": call.response, "tokens_in": call.tokens_in, "tokens_out": call.tokens_out,
        "cost_usd": call.cost_usd, "latency_ms": call.latency_ms, "error": call.error,
        "created_at": call.created_at,
    }


def reply_run_view(run: ReplyRun, llm_calls: list[LlmCall]) -> Json:
    return {
        "id": run.id, "status": run.status, "context": run.context, "plan": run.plan,
        "guard_verdicts": run.guard_verdicts, "error": run.error,
        "llm_calls": [llm_call_view(c) for c in llm_calls],
    }


def persona_view(persona: Persona) -> Json:
    """All columns of `persona` except the audit timestamps."""
    return {"id": persona.id, **{f: getattr(persona, f) for f in PERSONA_FIELDS}}


def channel_view(channel: Channel) -> Json:
    """Never includes `credentials_enc`."""
    return {
        "id": channel.id, "kind": channel.kind, "title": channel.title,
        "status": channel.status, "account_label": channel.account_label,
        "last_error": channel.last_error, "last_activity_at": channel.last_activity_at,
    }
