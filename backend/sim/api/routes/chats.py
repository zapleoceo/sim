"""`/api/chats/*` and `/api/reply-runs/{id}`: the chat list, one chat's timeline, what the
operator does in a chat (write as the persona, AI on/off, «Отвечать сразу», notes, read,
done)."""
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
from sim.services.ai_switch import plan_wakes, wake
from sim.services.media_library import add_media
from sim.services.outbox import enqueue_message
from sim.services.persona_status import chat_status

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
async def list_chats(db: AsyncSession, ctx: ApiContext, q: str | None = None,
                     limit: Limit = 50, before: datetime | None = None,
                     persona_id: int | None = None) -> Json:
    """Newest activity first; `persona_id` keeps the chats that persona answers in. Every row
    carries `persona_status` (statuses of the page in a fixed number of queries)."""
    stmt = search_clients(select(Chat, Client).join(Client, Client.id == Chat.client_id), q)
    if persona_id is not None:
        stmt = stmt.where(Chat.persona_id == persona_id)
    if before is not None:
        stmt = stmt.where(Chat.last_message_at < before)
    stmt = stmt.order_by(Chat.last_message_at.desc().nulls_last(), Chat.id.desc()).limit(limit)
    pairs = [(chat, client) for chat, client in (await db.execute(stmt)).all()]
    return {"items": await chat_rows(db, pairs, ctx.deps.clock.now())}


@get("/chats/{chat_id:int}")
async def get_chat(chat_id: int, db: AsyncSession, ctx: ApiContext) -> Json:
    chat = await get_or_404(db, Chat, chat_id, "Чат")
    client = await get_or_404(db, Client, chat.client_id, "Клиент")
    row = (await chat_rows(db, [(chat, client)], ctx.deps.clock.now()))[0]
    return {**row, "client": views.client_view(client)}


@get("/chats/{chat_id:int}/status")
async def get_status(chat_id: int, db: AsyncSession, ctx: ApiContext) -> Json:
    """«Статус Марины» of one chat, for the team only (docs/attention.md, «Статус для
    команды»): the dashboard re-reads it on the chat's live events and every 30 s."""
    await get_or_404(db, Chat, chat_id, "Чат")
    return (await chat_status(db, ctx.deps, chat_id)).as_json()


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
        return await chat_row(db, chat, ctx.deps.clock.now())
    chat.ai_enabled = data.enabled
    await append_event(db, chat_id=chat.id, kind=EventKind.AI_TOGGLED, actor=admin.actor,
                       data={"enabled": data.enabled, "scope": "chat"})
    waiting = await plan_wakes(db, ctx.deps, [chat.id]) if data.enabled else []
    row = await chat_row(db, chat, ctx.deps.clock.now())
    await db.commit()
    await wake(ctx.deps, waiting)
    return row


@post("/chats/{chat_id:int}/attention-instant", status_code=200)
async def toggle_attention_instant(chat_id: int, data: AiToggleIn, db: AsyncSession,
                                   ctx: ApiContext) -> Json:
    """«Отвечать сразу» in this chat only: no phone checks, she answers like «Всегда сразу»
    (docs/attention.md; for testing)."""
    chat = await get_or_404(db, Chat, chat_id, "Чат")
    chat.attention_instant = data.enabled
    return await chat_row(db, chat, ctx.deps.clock.now())


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
async def operator_done(chat_id: int, db: AsyncSession, ctx: ApiContext,
                        admin: AdminContext) -> Json:
    """«Оператор разобрался»: the AI answers here again — a journal line, and what the client
    wrote meanwhile is picked up like after switching the AI on (services/ai_switch)."""
    chat = await get_or_404(db, Chat, chat_id, "Чат")
    if not chat.needs_operator:
        return await chat_row(db, chat, ctx.deps.clock.now())
    chat.needs_operator = False
    await append_event(db, chat_id=chat.id, kind=EventKind.AI_TOGGLED, actor=admin.actor,
                       data={"enabled": True, "scope": "operator"})
    waiting = await plan_wakes(db, ctx.deps, [chat.id])
    row = await chat_row(db, chat, ctx.deps.clock.now())
    await db.commit()
    await wake(ctx.deps, waiting)
    return row


@get("/reply-runs/{run_id:int}")
async def get_reply_run(run_id: int, db: AsyncSession) -> Json:
    run = await get_or_404(db, ReplyRun, run_id, "Ход персонажа")
    calls = list(await db.scalars(
        select(LlmCall).where(LlmCall.reply_run_id == run.id).order_by(LlmCall.id)))
    return views.reply_run_view(run, calls)


ROUTES = [list_chats, get_chat, get_status, get_timeline, send_message, toggle_ai,
          toggle_attention_instant, mark_read, add_note, operator_done, get_reply_run]
