"""Seeded rows for the channel tests: a persona with an Instagram and a WhatsApp channel and one
client who writes in both."""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Channel, Chat, Client, ClientIdentity, Persona
from sim.domain.enums import ChannelKind, ChannelStatus
from tests.fakes.telegram import TestSessions

WA_JID = "79001234567@s.whatsapp.net"


@dataclass
class World:
    persona: Persona
    instagram: Channel
    whatsapp: Channel
    client: Client
    ig_chat: Chat
    wa_chat: Chat


async def seed_world(db: AsyncSession) -> World:
    persona = Persona(name="Марина", ai_enabled=True)
    db.add(persona)
    await db.flush()
    instagram = Channel(persona_id=persona.id, kind=ChannelKind.INSTAGRAM, title="Марина IG",
                        status=ChannelStatus.CONNECTED, credentials_enc="token")
    whatsapp = Channel(persona_id=persona.id, kind=ChannelKind.WHATSAPP, title="Марина WA",
                       status=ChannelStatus.CONNECTED, credentials_enc="token")
    client = Client(persona_id=persona.id, name="Дима", ai_enabled=True)
    db.add_all([instagram, whatsapp, client])
    await db.flush()
    ig_chat = Chat(persona_id=persona.id, client_id=client.id, channel_id=instagram.id,
                   external_thread_id="340282366841710300949128")
    wa_chat = Chat(persona_id=persona.id, client_id=client.id, channel_id=whatsapp.id,
                   external_thread_id=WA_JID)
    db.add_all([
        ClientIdentity(client_id=client.id, channel_kind=ChannelKind.TELEGRAM_USER,
                       external_id="555", access_hash="9"),
        ClientIdentity(client_id=client.id, channel_kind=ChannelKind.INSTAGRAM,
                       external_id="1001"),
        ClientIdentity(client_id=client.id, channel_kind=ChannelKind.WHATSAPP,
                       external_id=WA_JID),
        ig_chat, wa_chat,
    ])
    await db.flush()
    await db.commit()          # a SAVEPOINT: code under test opens its own transactions
    return World(persona, instagram, whatsapp, client, ig_chat, wa_chat)


@pytest.fixture
def sessions(db: AsyncSession) -> TestSessions:
    return TestSessions(db)


@pytest.fixture
async def world(db: AsyncSession) -> World:
    return await seed_world(db)
