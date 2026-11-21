"""`/api/chats/*` and `/api/reply-runs/{id}`: the chat list, one chat's timeline, what the
operator does in a chat (write as the persona, AI on/off, notes, read, done)."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

import msgspec
from litestar import Request, get, post
from litestar.exceptions import ClientException
from litestar.params import Parameter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api import views
from sim.api.context import AdminContext, ApiContext
from sim.api.queries import (
    chat_row,
    chat_rows,
    get_or_404,
    message_item,
    search_clients,
    timeline_items,
)
from sim.api.schemas import AiToggleIn, TextIn
from sim.api.uploads import read_upload, upload_mime
from sim.db.models import Chat, ChatEvent, Client, LlmCall, ReplyRun
from sim.domain.enums import Author, EventKind, MediaKind, MediaSource, MessageKind
from sim.events import append_event
from sim.services.ai_switch import chats_to_wake, wake
from sim.services.media_library import add_media
from sim.services.outbox import enqueue_message

Json = dict[str, Any]
Limit = Annotated[int, Parameter(ge=1, le=200)]

# What an uploaded file is sent as in Telegram.
MESSAGE_KIND_BY_MEDIA = {
    MediaKind.PHOTO: MessageKind.PHOTO,
    MediaKind.VIDEO: MessageKind.VIDEO,
    MediaKind.AUDIO: MessageKind.FILE,
    MediaKind.FILE: MessageKind.FILE,
}


@get("/chats")
async def list_chats(db: AsyncSession, q: str | None = None, limit: Limit = 50,
                     before: datetime | None = None, persona_id: int | None = None) -> Json:
    """Newest activity first; `persona_id` keeps the chats that persona answers in."""
    stmt = search_clients(select(Chat, Client).join(Client, Client.id == Chat.client_id), q)
    if persona_id is not None:
        stmt = stmt.where(Chat.persona_id == persona_id)
    if before is not None:
        stmt = stmt.where(Chat.last_message_at < before)
    stmt = stmt.order_by(Chat.last_message_at.desc().nulls_last(), Chat.id.desc()).limit(limit)
    pairs = [(chat, client) for chat, client in (await db.execute(stmt)).all()]
    return {"items": await chat_rows(db, pairs)}


@get("/chats/{chat_id:int}")
async def get_chat(chat_id: int, db: AsyncSession) -> Json:
    chat = await get_or_404(db, Chat, chat_id, "Чат")
    client = await get_or_404(db, Client, chat.client_id, "Клиент")
    return {**(await chat_rows(db, [(chat, client)]))[0], "client": views.client_view(client)}


@get("/chats/{chat_id:int}/timeline")
async def get_timeline(chat_id: int, db: AsyncSession, before_id: int | None = None,
                       limit: Limit = 50) -> Json:
    await get_or_404(db, Chat, chat_id, "Чат")
    stmt = select(ChatEvent).where(ChatEvent.chat_id == chat_id)
    if before_id is not None:
        stmt = stmt.where(ChatEvent.id < before_id)
    events = list(await db.scalars(stmt.order_by(ChatEvent.id.desc()).limit(limit)))
    return {"items": await timeline_items(db, events)}


@post("/chats/{chat_id:int}/messages", status_code=201)
async def send_message(chat_id: int, request: Request[Any, Any, Any], ctx: ApiContext,
                       db: AsyncSession, admin: AdminContext) -> Json:
    """The operator writes as the persona: `{text}` JSON, or multipart `file` + `caption`.
    An uploaded file is stored in the persona's library (source `library`, not a reference),
    so it is also offered to the reply model later. The send task is deferred only after
    the message row is committed, so the messenger process always finds it."""
    chat = await get_or_404(db, Chat, chat_id, "Чат")
    media_type, _ = request.content_type
    if media_type.startswith("multipart/"):
        form = await request.form()
        upload, data = await read_upload(form)
        caption = str(form.get("caption") or "").strip()
        media = await add_media(
            db, ctx.deps.storage, ctx.deps.tools, persona_id=chat.persona_id, data=data,
            mime=upload_mime(upload), file_name=upload.filename or "",
            source=MediaSource.LIBRARY, caption=caption)
        message = await enqueue_message(
            db, chat_id=chat.id, author=Author.OPERATOR, kind=MESSAGE_KIND_BY_MEDIA[media.kind],
            text=caption, media_id=media.id)
    else:
        try:
            body = msgspec.json.decode(await request.body(), type=TextIn)
        except msgspec.DecodeError as exc:
            raise ClientException(detail=f"Нужен текст сообщения: {exc}") from exc
        message = await enqueue_message(db, chat_id=chat.id, author=Author.OPERATOR,
                                        kind=MessageKind.TEXT, text=body.text.strip())
    await db.commit()
    await ctx.deps.tasks.send(message.id, chat.id)
    return await message_item(db, message, admin.actor)


@post("/chats/{chat_id:int}/ai", status_code=200)
async def toggle_ai(chat_id: int, data: AiToggleIn, ctx: ApiContext, db: AsyncSession,
                    admin: AdminContext) -> Json:
    """Switching the AI on also answers what arrived while it was off (services/ai_switch)."""
    chat = await get_or_404(db, Chat, chat_id, "Чат")
    if chat.ai_enabled == data.enabled:
        return await chat_row(db, chat)
    chat.ai_enabled = data.enabled
    await append_event(db, chat_id=chat.id, kind=EventKind.AI_TOGGLED, actor=admin.actor,
                       data={"enabled": data.enabled, "scope": "chat"})
    waiting = await chats_to_wake(db, [chat.id]) if data.enabled else []
    row = await chat_row(db, chat)
    await db.commit()
    await wake(ctx.deps.tasks, waiting)
    return row


@post("/chats/{chat_id:int}/read", status_code=204)
async def mark_read(chat_id: int, db: AsyncSession) -> None:
    chat = await get_or_404(db, Chat, chat_id, "Чат")
    chat.unread_count = 0


@post("/chats/{chat_id:int}/notes", status_code=201)
async def add_note(chat_id: int, data: TextIn, db: AsyncSession, admin: AdminContext) -> Json:
    chat = await get_or_404(db, Chat, chat_id, "Чат")
    event = await append_event(db, chat_id=chat.id, kind=EventKind.OPERATOR_NOTE,
                               actor=admin.actor, data={"text": data.text.strip()})
    return (await timeline_items(db, [event]))[0]


@post("/chats/{chat_id:int}/operator-done", status_code=200)
async def operator_done(chat_id: int, db: AsyncSession) -> Json:
    chat = await get_or_404(db, Chat, chat_id, "Чат")
    chat.needs_operator = False
    return await chat_row(db, chat)


@get("/reply-runs/{run_id:int}")
async def get_reply_run(run_id: int, db: AsyncSession) -> Json:
    run = await get_or_404(db, ReplyRun, run_id, "Ход персонажа")
    calls = list(await db.scalars(
        select(LlmCall).where(LlmCall.reply_run_id == run.id).order_by(LlmCall.id)))
    return views.reply_run_view(run, calls)


ROUTES = [list_chats, get_chat, get_timeline, send_message, toggle_ai, mark_read, add_note,
          operator_done, get_reply_run]
