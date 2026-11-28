"""`/api/clients/*`: the leads (docs/leads.md) — the table with filters, one lead's card and the
operator's edits (profile, AI switch, passport). An edit writes `client_updated` and/or
`ai_toggled` into the journal of every chat of that client, so the agent and the operator see
it in each timeline (docs/context.md §1). Passport fields the operator edits are locked for the
passport writer.

One person in many messengers (docs/leads.md): `/clients/matches` and `/clients/{id}/matches`
suggest clients that may be one person, `/merge` joins two of them, `/distinct` hides a
suggestion, `/timeline` is the journal of every chat of the client in one stream."""
from __future__ import annotations

from typing import Annotated, Any

from litestar import get, patch, post
from litestar.exceptions import ClientException, NotFoundException
from litestar.params import Parameter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api import views
from sim.api.context import AdminContext, ApiContext
from sim.api.queries import (
    by_ids,
    chats_of_client,
    get_or_404,
    search_clients,
    stream_items,
)
from sim.api.schemas import ClientPatchIn, OtherClientIn, check_timezone, sent_fields
from sim.db.models import Chat, Client, Message, Persona
from sim.domain.client_match import Suggestion
from sim.domain.enums import EventKind, LeadStage
from sim.domain.passport import apply_edit, passport_of, stored
from sim.events import append_event
from sim.services.ai_switch import plan_wakes, wake
from sim.services.client_match import mark_distinct, suggestions
from sim.services.client_merge import ClientMissing, MergeRefused, merge_clients

Json = dict[str, Any]
CLIENTS_LIMIT = 1000
Limit = Annotated[int, Parameter(ge=1, le=200)]


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


async def client_detail(db: AsyncSession, ctx: ApiContext, client: Client) -> Json:
    persona = await db.get(Persona, client.persona_id)
    return {**views.client_view(client), "persona": views.persona_brief(persona),
            "messengers": views.messengers(client),
            "chats": await chats_of_client(db, client, ctx.deps.clock.now())}


@get("/clients/{client_id:int}")
async def get_client(client_id: int, db: AsyncSession, ctx: ApiContext) -> Json:
    return await client_detail(db, ctx, await get_or_404(db, Client, client_id, "Клиент"))


async def suggestion_items(db: AsyncSession, found: list[Suggestion]) -> list[Json]:
    clients = await by_ids(db, Client, (i for s in found for i in (s.client_id, s.other_id)))
    personas = await by_ids(db, Persona, (c.persona_id for c in clients.values()))
    return [{"clients": [views.match_brief(clients[i], personas.get(clients[i].persona_id))
                         for i in (s.client_id, s.other_id)],
             "reasons": [{"kind": r.kind, "text": r.text} for r in s.reasons]}
            for s in found]


@get("/clients/matches")
async def list_matches(db: AsyncSession) -> Json:
    """«Возможно, один человек»: every pair of clients that may be one person, never merged by
    itself (docs/leads.md)."""
    return {"items": await suggestion_items(db, await suggestions(db))}


@get("/clients/{client_id:int}/matches")
async def client_matches(client_id: int, db: AsyncSession) -> Json:
    await get_or_404(db, Client, client_id, "Клиент")
    return {"items": await suggestion_items(db, await suggestions(db, client_id))}


@post("/clients/{client_id:int}/merge", status_code=200)
async def merge_client(client_id: int, data: OtherClientIn, db: AsyncSession, ctx: ApiContext,
                       admin: AdminContext) -> Json:
    """Merge `other_id` into this client: its identities and chats (with their memory) move
    here, the profile and the passport are combined, the other client is deleted. 409 — both
    have an account in one messenger (or it is the same client); 404 — no such client."""
    try:
        result = await merge_clients(db, client_id, data.other_id, actor=admin.actor,
                                     now=ctx.deps.clock.now())
    except ClientMissing as exc:
        raise NotFoundException(detail=f"Клиент не найден: {exc}") from exc
    except MergeRefused as exc:
        raise ClientException(detail=str(exc), status_code=409) from exc
    return await client_detail(db, ctx, result.client)


@post("/clients/{client_id:int}/distinct", status_code=204)
async def mark_clients_distinct(client_id: int, data: OtherClientIn, db: AsyncSession) -> None:
    """«Это разные люди»: the pair is not suggested again."""
    await get_or_404(db, Client, client_id, "Клиент")
    await get_or_404(db, Client, data.other_id, "Клиент")
    if data.other_id == client_id:
        raise ClientException(detail="Нужен другой клиент")
    await mark_distinct(db, client_id, data.other_id)


@get("/clients/{client_id:int}/timeline")
async def client_timeline(client_id: int, db: AsyncSession, before_id: int | None = None,
                          limit: Limit = 50) -> Json:
    """The journal of every chat of the client in one stream, newest first (event ids grow
    across chats); each item names its chat and messenger."""
    await get_or_404(db, Client, client_id, "Клиент")
    chats = list(await db.scalars(select(Chat).where(Chat.client_id == client_id)))
    return {"items": await stream_items(db, chats, before_id=before_id, limit=limit)}


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
    chat_ids: list[int] = []
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
    # Switching the AI on answers what arrived while it was off (services/ai_switch).
    switched_on = changes.get("ai_enabled") is True
    waiting = await plan_wakes(db, ctx.deps, chat_ids) if switched_on else []
    view = views.client_view(client)
    await db.commit()
    await wake(ctx.deps, waiting)
    return view


ROUTES = [list_clients, get_client, list_matches, client_matches, merge_client,
          mark_clients_distinct, client_timeline, update_client]
