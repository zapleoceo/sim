"""Seeded rows for the Telegram tests: a persona with a connected channel and one client chat."""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Channel, Chat, Client, ClientIdentity, Persona
from sim.domain.enums import ChannelKind, ChannelStatus
from tests.fakes.telegram import TestSessions


@dataclass
class World:
    persona: Persona
    channel: Channel
    client: Client
    identity: ClientIdentity
    chat: Chat


async def seed_world(db: AsyncSession, *, client_ai: bool = True, chat_ai: bool = True,
                     persona_calls: bool = True, live_model: str = "gemini-live",
                     channel_status: ChannelStatus = ChannelStatus.CONNECTED,
                     credentials: str | None = "token") -> World:
    persona = Persona(name="Марина", agents={"live": {"model": live_model}},
                      calls_enabled=persona_calls,
                      ai_enabled=True)
    db.add(persona)
    await db.flush()
    channel = Channel(persona_id=persona.id, kind=ChannelKind.TELEGRAM_USER, title="Марина TG",
                      status=channel_status, credentials_enc=credentials)
    client = Client(persona_id=persona.id, name="Дима", ai_enabled=client_ai)
    db.add_all([channel, client])
    await db.flush()
    identity = ClientIdentity(client_id=client.id, channel_kind=ChannelKind.TELEGRAM_USER,
                              external_id="1001", access_hash="555")
    chat = Chat(persona_id=persona.id, client_id=client.id, channel_id=channel.id,
                ai_enabled=chat_ai)
    db.add_all([identity, chat])
    await db.flush()
    await db.commit()          # a SAVEPOINT: code under test opens its own transactions
    return World(persona, channel, client, identity, chat)


@pytest.fixture
def sessions(db: AsyncSession) -> TestSessions:
    return TestSessions(db)
