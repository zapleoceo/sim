"""«Возможно, один человек» from the database (docs/leads.md): what every client is known by
(profile, passport, identities) and what they said (their messages that name a @username or a
phone, the passport text), compared by sim.domain.client_match. Pairs the operator marked
«разные люди» (`client_distinct`) are not offered again."""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, Client, ClientDistinct, Message
from sim.domain.client_match import (
    Profile,
    Suggestion,
    build_profile,
    pair,
    suggest,
    whatsapp_phone,
)
from sim.domain.enums import ChannelKind, Direction
from sim.domain.passport import passport_of

CLIENTS_LIMIT = 2000            # the newest clients compared
MENTION_MESSAGES = 5000         # the newest client messages that name a handle or a phone
# Only messages that may name someone are read (PostgreSQL regex, the same idea as the
# domain's HANDLE_RE / PHONE_RE).
MENTION_PATTERN = r"(@|t\.me/|instagram\.com/)[A-Za-z0-9_.]{3}|\d[\d\s()+-]{7,}\d"


async def load_profiles(session: AsyncSession) -> dict[int, Profile]:
    clients = list(await session.scalars(
        select(Client).order_by(Client.last_seen_at.desc().nulls_last(), Client.id.desc())
        .limit(CLIENTS_LIMIT)))
    ids = [c.id for c in clients]
    texts: dict[int, list[str]] = defaultdict(list)
    rows = await session.execute(
        select(Chat.client_id, Message.text).join(Chat, Chat.id == Message.chat_id)
        .where(Chat.client_id.in_(ids), Message.direction == Direction.IN,
               Message.text.op("~")(MENTION_PATTERN))
        .order_by(Message.id.desc()).limit(MENTION_MESSAGES))
    for client_id, text in rows:
        texts[client_id].append(text)
    profiles = {}
    for client in clients:
        passport = passport_of(client.passport)
        whatsapp = [whatsapp_phone(i.external_id) for i in client.identities
                    if i.channel_kind == ChannelKind.WHATSAPP]
        profiles[client.id] = build_profile(
            client.id, messengers=[i.channel_kind for i in client.identities],
            phones=[client.phone, *whatsapp], handles=[client.username],
            names=[client.name, passport.name], texts=[*texts[client.id], passport.text])
    return profiles


async def distinct_pairs(session: AsyncSession) -> list[tuple[int, int]]:
    rows = await session.execute(select(ClientDistinct.client_id, ClientDistinct.other_id))
    return [(a, b) for a, b in rows]


async def suggestions(session: AsyncSession, client_id: int | None = None) -> list[Suggestion]:
    """Every pair that may be one person; with `client_id` only the pairs of that client."""
    found = suggest(await load_profiles(session), await distinct_pairs(session))
    if client_id is None:
        return found
    return [s for s in found if client_id in (s.client_id, s.other_id)]


async def mark_distinct(session: AsyncSession, client_id: int, other_id: int) -> None:
    """«Это разные люди»: the pair is stored once (smaller id first); repeating is harmless."""
    low, high = pair(client_id, other_id)
    await session.execute(insert(ClientDistinct).values(client_id=low, other_id=high)
                          .on_conflict_do_nothing(index_elements=["client_id", "other_id"]))
