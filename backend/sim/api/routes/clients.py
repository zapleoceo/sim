"""`/api/clients/*`: the people the persona talks to. An edit writes `client_updated` and/or
`ai_toggled` into the journal of every chat of that client, so the agent and the operator see
it in each timeline (docs/context.md §1)."""
from __future__ import annotations

from typing import Any

from litestar import get, patch
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api import views
from sim.api.context import AdminContext, ApiContext
from sim.api.queries import chats_of_client, get_or_404, search_clients
from sim.api.schemas import ClientPatchIn, MemoryItemIn, check_timezone, sent_fields
from sim.db.models import Chat, Client
from sim.domain.enums import EventKind
from sim.events import append_event

Json = dict[str, Any]
CLIENTS_LIMIT = 1000


def memory_rows(items: list[MemoryItemIn], now_iso: str) -> list[Json]:
    return [{"fact": item.fact.strip(), "event_id": item.event_id, "at": item.at or now_iso}
            for item in items if item.fact.strip()]


@get("/clients")
async def list_clients(db: AsyncSession, q: str | None = None) -> Json:
    stmt = search_clients(select(Client), q).order_by(
        Client.last_seen_at.desc().nulls_last(), Client.id.desc()).limit(CLIENTS_LIMIT)
    clients = list(await db.scalars(stmt))
    counts = dict((await db.execute(
        select(Chat.client_id, func.count(Chat.id))
        .where(Chat.client_id.in_([c.id for c in clients])).group_by(Chat.client_id))).all())
    return {"items": [{**views.client_view(c), "chats_count": counts.get(c.id, 0)}
                      for c in clients]}


@get("/clients/{client_id:int}")
async def get_client(client_id: int, db: AsyncSession) -> Json:
    client = await get_or_404(db, Client, client_id, "Клиент")
    return {**views.client_view(client), "chats": await chats_of_client(db, client)}


@patch("/clients/{client_id:int}")
async def update_client(client_id: int, data: ClientPatchIn, ctx: ApiContext, db: AsyncSession,
                        admin: AdminContext) -> Json:
    client = await get_or_404(db, Client, client_id, "Клиент")
    sent = sent_fields(data)
    if "timezone" in sent:
        sent["timezone"] = (sent["timezone"] or "").strip() or None
        if sent["timezone"]:
            check_timezone(sent["timezone"])
    if "memory" in sent:
        sent["memory"] = memory_rows(sent["memory"], ctx.deps.clock.now().isoformat())
    changes = {f: v for f, v in sent.items() if getattr(client, f) != v}
    for field, value in changes.items():
        setattr(client, field, value)
    profile_fields = sorted(f for f in changes if f != "ai_enabled")
    if changes:
        chat_ids = list(await db.scalars(select(Chat.id).where(Chat.client_id == client.id)))
        for chat_id in chat_ids:
            if profile_fields:
                await append_event(db, chat_id=chat_id, kind=EventKind.CLIENT_UPDATED,
                                   actor=admin.actor, data={"fields": profile_fields})
            if "ai_enabled" in changes:
                await append_event(db, chat_id=chat_id, kind=EventKind.AI_TOGGLED,
                                   actor=admin.actor,
                                   data={"enabled": client.ai_enabled, "scope": "client"})
    await db.flush()
    return views.client_view(client)


ROUTES = [list_clients, get_client, update_client]
