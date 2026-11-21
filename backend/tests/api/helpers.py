"""Builders shared by the API tests: a signed Telegram widget payload, seed rows, login."""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from litestar import Litestar
from litestar.testing import AsyncTestClient
from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.db.models import AdminUser, Channel, Chat, Client, Message, Persona
from sim.domain.enums import (
    Author,
    ChannelKind,
    ChannelStatus,
    Direction,
    EventKind,
    MessageKind,
    MessageStatus,
)
from sim.events import append_event

BOT_TOKEN = "123456:test-login-bot"
ADMIN_TG_ID = 1001

ApiClient = AsyncTestClient[Litestar]


def signed_widget(tg_id: int, *, auth_date: int, token: str = BOT_TOKEN,
                  **fields: Any) -> dict[str, Any]:
    """What the Telegram Login Widget posts, signed like Telegram does."""
    data: dict[str, Any] = {"id": tg_id, "first_name": "Дима", "username": "dima",
                            "auth_date": auth_date, **fields}
    pairs = sorted((k, str(v)) for k, v in data.items() if v not in (None, ""))
    check_string = "\n".join(f"{k}={v}" for k, v in pairs)
    secret = hashlib.sha256(token.encode()).digest()
    return {**data, "hash": hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()}


async def make_admin(db: AsyncSession, tg_id: int = ADMIN_TG_ID, *,
                     active: bool = True) -> int:
    admin = AdminUser(telegram_id=tg_id, name="Admin", is_active=active)
    db.add(admin)
    await db.commit()
    return admin.id


async def login(client: ApiClient, clock: FixedClock, tg_id: int = ADMIN_TG_ID) -> dict[str, Any]:
    """Log in through the widget route and send the CSRF token with every later request."""
    response = await client.post(
        "/api/auth/telegram", json=signed_widget(tg_id, auth_date=int(clock.now().timestamp())))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    client.headers["X-CSRF-Token"] = body["csrf"]
    return body


@dataclass(frozen=True)
class World:
    """Ids of one persona with one Telegram channel, one client, one chat and one message."""

    persona_id: int
    channel_id: int
    client_id: int
    chat_id: int
    message_id: int
    event_id: int


def pp(world: World, path: str = "") -> str:
    """`/api/personas/<world persona><path>`: every persona-scoped route."""
    return f"/api/personas/{world.persona_id}{path}"


async def seed_world(db: AsyncSession, clock: FixedClock) -> World:
    # The daily planner is off, so a test turns it on (or plans days) only when it wants to.
    persona = Persona(name="Марина", prompt="Ты Марина.",
                      agents={"reply": {"model": "google/reply"}, "life": {"model": ""}})
    db.add(persona)
    await db.flush()
    channel = Channel(persona_id=persona.id, kind=ChannelKind.TELEGRAM_USER, title="Марина",
                      status=ChannelStatus.CONNECTED, account_label="@marina",
                      credentials_enc="encrypted-blob")
    client = Client(persona_id=persona.id, name="Дима Петров", username="dima_p",
                    ai_enabled=True)
    db.add_all([channel, client])
    await db.flush()
    chat = Chat(persona_id=persona.id, client_id=client.id, channel_id=channel.id,
                unread_count=3, needs_operator=True, last_message_at=clock.now())
    db.add(chat)
    await db.flush()
    message = Message(chat_id=chat.id, direction=Direction.IN, author=Author.CLIENT,
                      kind=MessageKind.TEXT, text="привет", status=MessageStatus.RECEIVED)
    db.add(message)
    await db.flush()
    event = await append_event(db, chat_id=chat.id, kind=EventKind.MESSAGE_IN, actor="client",
                               message_id=message.id)
    await db.commit()
    return World(persona_id=persona.id, channel_id=channel.id, client_id=client.id,
                 chat_id=chat.id, message_id=message.id, event_id=event.id)
