"""`/api/chats/{id}/contacts`: «Запланировать» in a chat (docs/initiative.md, docs/api.md).
The manager plans that she writes or calls the client at a time about a topic; her own calls
(«давай созвонимся») show up in the same list. A planned contact can be cancelled until it
starts; the minute tick (`agent.contacts_tick`) starts the due ones."""
from __future__ import annotations

from datetime import UTC
from typing import Any

from litestar import get, post
from litestar.exceptions import ClientException
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api.context import AdminContext, ApiContext
from sim.api.queries import get_or_404
from sim.api.schemas import ContactIn
from sim.db.models import Chat, PlannedContact
from sim.domain.enums import ContactKind, ContactSource
from sim.services.initiative import (
    InitiativeError,
    cancel_contact,
    chat_contacts,
    contact_view,
    plan_contact,
)

Json = dict[str, Any]


@get("/chats/{chat_id:int}/contacts")
async def list_contacts(chat_id: int, db: AsyncSession) -> Json:
    await get_or_404(db, Chat, chat_id, "Чат")
    return {"items": [contact_view(c) for c in await chat_contacts(db, chat_id)]}


@post("/chats/{chat_id:int}/contacts", status_code=201)
async def create_contact(chat_id: int, data: ContactIn, db: AsyncSession, ctx: ApiContext,
                         admin: AdminContext) -> Json:
    chat = await get_or_404(db, Chat, chat_id, "Чат")
    due_at = data.due_at if data.due_at.tzinfo else data.due_at.replace(tzinfo=UTC)
    try:
        contact = await plan_contact(
            db, chat, kind=ContactKind(data.kind), topic=data.topic, due_at=due_at,
            source=ContactSource.MANAGER, created_by=admin.actor, now=ctx.deps.clock.now(),
            calls_enabled=ctx.settings.calls_enabled)
    except InitiativeError as exc:
        raise ClientException(detail=str(exc)) from exc
    return contact_view(contact)


@post("/chats/{chat_id:int}/contacts/{contact_id:int}/cancel", status_code=200)
async def cancel(chat_id: int, contact_id: int, db: AsyncSession, ctx: ApiContext) -> Json:
    contact = await get_or_404(db, PlannedContact, contact_id, "Контакт")
    if contact.chat_id != chat_id:
        raise ClientException(detail="Контакт из другого чата")
    try:
        cancel_contact(contact, ctx.deps.clock.now())
    except InitiativeError as exc:
        raise ClientException(detail=str(exc), status_code=409) from exc
    return contact_view(contact)


ROUTES = [list_contacts, create_contact, cancel]
