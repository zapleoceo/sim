"""`/api/clients/*`: the leads (docs/leads.md) — the table with filters, one lead's card and the
operator's edits (profile, AI switch, passport). An edit writes `client_updated` and/or
`ai_toggled` into the journal of every chat of that client, so the agent and the operator see
it in each timeline (docs/context.md §1). Passport fields the operator edits are locked for the
passport writer."""
from __future__ import annotations

from typing import Any

from litestar import get, patch
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api import views
from sim.api.context import AdminContext, ApiContext
from sim.api.queries import by_ids, chats_of_client, get_or_404, search_clients
from sim.api.schemas import ClientPatchIn, check_timezone, sent_fields
from sim.db.models import Chat, Client, Message, Persona
from sim.domain.enums import EventKind, LeadStage
from sim.domain.passport import apply_edit, passport_of, stored
from sim.events import append_event

Json = dict[str, Any]
CLIENTS_LIMIT = 1000


def passport_changes(data: Any) -> dict[str, Any]:
    """The sent passport fields, trimmed; a time zone must be real (empty clears it)."""
    sent = sent_fields(data)
    for key, value in list(sent.items()):
        if isinstance(value, str) and key != "text":
            sent[key] = " ".join(value.split())
    if sent.get("timezone"):
        check_timezone(sent["timezone"])
    return sent


@get("/clients")
async def list_clients(db: AsyncSession, q: str | None = None, persona_id: int | None = None,
                       stage: LeadStage | None = None) -> Json:
    """Leads: newest contact first, with the persona, chats and messages counts."""
    stmt = search_clients(select(Client), q)
    if persona_id is not None:
        stmt = stmt.where(Client.persona_id == persona_id)
    if stage is not None:
        stage_of = func.coalesce(Client.passport["stage"].astext, LeadStage.NEW.value)
        stmt = stmt.where(stage_of == stage.value)
    stmt = stmt.order_by(Client.last_seen_at.desc().nulls_last(), Client.id.desc())
    clients = list(await db.scalars(stmt.limit(CLIENTS_LIMIT)))
    ids = [c.id for c in clients]
    chats = dict((await db.execute(
        select(Chat.client_id, func.count(Chat.id))
        .where(Chat.client_id.in_(ids)).group_by(Chat.client_id))).all())
    messages = dict((await db.execute(
        select(Chat.client_id, func.count(Message.id)).join(Message, Message.chat_id == Chat.id)
        .where(Chat.client_id.in_(ids)).group_by(Chat.client_id))).all())
    personas = await by_ids(db, Persona, (c.persona_id for c in clients))
    return {"items": [
        {**views.client_view(c), "persona": views.persona_brief(personas.get(c.persona_id)),
         "chats_count": chats.get(c.id, 0), "messages_count": messages.get(c.id, 0)}
        for c in clients]}


@get("/clients/{client_id:int}")
async def get_client(client_id: int, db: AsyncSession) -> Json:
    client = await get_or_404(db, Client, client_id, "Клиент")
    persona = await db.get(Persona, client.persona_id)
    return {**views.client_view(client), "persona": views.persona_brief(persona),
            "chats": await chats_of_client(db, client)}


@patch("/clients/{client_id:int}")
async def update_client(client_id: int, data: ClientPatchIn, ctx: ApiContext, db: AsyncSession,
                        admin: AdminContext) -> Json:
    client = await get_or_404(db, Client, client_id, "Клиент")
    sent = sent_fields(data)
    if "timezone" in sent:
        sent["timezone"] = (sent["timezone"] or "").strip() or None
        if sent["timezone"]:
            check_timezone(sent["timezone"])
    if "passport" in sent:
        edit = passport_changes(sent["passport"])
        old = passport_of(client.passport)
        sent["passport"] = stored(apply_edit(old, edit, ctx.deps.clock.now())) if edit \
            else client.passport
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
