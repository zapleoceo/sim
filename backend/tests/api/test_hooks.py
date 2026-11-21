"""`POST /api/hooks/evolution/{channel_id}` (sim/api/routes/hooks.py): public, but only with the
channel's own secret; messages go to the Inbox like Telegram's, connection events move the
channel's status (docs/channels.md, «Приём»)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.channels.whatsapp.evolution import SECRET_HEADER
from sim.crypto import encrypt_json
from sim.db.models import Channel, Chat, ClientIdentity, Message
from sim.domain.enums import ChannelKind, ChannelStatus, Direction
from tests.api.fakes import FakeDeps
from tests.api.helpers import ApiClient, World

JID = "79001234567@s.whatsapp.net"
SECRET = "s3cret-per-channel"


async def whatsapp_channel(db: AsyncSession, deps: FakeDeps, world: World, *,
                           status: ChannelStatus = ChannelStatus.CONNECTED) -> Channel:
    channel = Channel(persona_id=world.persona_id, kind=ChannelKind.WHATSAPP, title="Марина WA",
                      status=status, credentials_enc=encrypt_json(
                          {"instance": "sim-x", "webhook_secret": SECRET},
                          deps.settings.secret_key))
    db.add(channel)
    await db.commit()
    return channel


def upsert(text: str = "привет из WhatsApp", mid: str = "WAM1") -> dict[str, Any]:
    return {"event": "messages.upsert", "instance": "sim-x", "data": {
        "key": {"remoteJid": "123456789@lid", "remoteJidAlt": JID, "fromMe": False, "id": mid},
        "pushName": "Дима", "message": {"conversation": text},
        "messageTimestamp": 1_763_750_000}}


async def hook(client: ApiClient, channel_id: int, payload: Any,
               secret: str | None = SECRET) -> Any:
    headers = {SECRET_HEADER: secret} if secret is not None else {}
    return await client.post(f"/api/hooks/evolution/{channel_id}", json=payload,
                             headers=headers)


async def test_only_the_channels_secret_gets_in(client: ApiClient, world: World,
                                                db: AsyncSession, deps: FakeDeps) -> None:
    channel = await whatsapp_channel(db, deps, world)
    assert (await hook(client, channel.id, upsert(), secret=None)).status_code == 403
    assert (await hook(client, channel.id, upsert(), secret="wrong")).status_code == 403
    assert (await hook(client, 999_999, upsert())).status_code == 404
    assert (await hook(client, world.channel_id, upsert())).status_code == 404   # Telegram
    count = await db.scalar(select(Message.id).where(Message.external_id == "WAM1"))
    assert count is None


async def test_a_message_goes_to_the_inbox_once(client: ApiClient, world: World,
                                                db: AsyncSession, deps: FakeDeps) -> None:
    channel = await whatsapp_channel(db, deps, world)
    first = await hook(client, channel.id, upsert())          # no dashboard login needed
    assert first.status_code == 200 and first.json() == {"ok": True, "stored": 1}
    assert (await hook(client, channel.id, upsert())).json()["stored"] == 0    # a repeat
    chat = await db.scalar(select(Chat).where(Chat.channel_id == channel.id))
    assert chat is not None and chat.external_thread_id == JID
    identity = await db.scalar(select(ClientIdentity).where(
        ClientIdentity.client_id == chat.client_id))
    assert identity is not None
    assert (identity.channel_kind, identity.external_id) == (ChannelKind.WHATSAPP, JID)
    message = await db.scalar(select(Message).where(Message.chat_id == chat.id))
    assert message is not None
    assert (message.direction, message.text, message.external_id) == (
        Direction.IN, "привет из WhatsApp", "WAM1")
    understand = [call for call in deps.tasks.calls if call[0] == "understand"]
    assert understand == []                                 # text only


async def test_connection_events_move_the_status(client: ApiClient, world: World,
                                                 db: AsyncSession, deps: FakeDeps) -> None:
    channel = await whatsapp_channel(db, deps, world)
    unlinked = {"event": "connection.update", "data": {"state": "close", "statusReason": 401}}
    assert (await hook(client, channel.id, unlinked)).status_code == 200
    await db.refresh(channel)
    assert channel.status == ChannelStatus.CHALLENGE and "заново" in (channel.last_error or "")
    await db.commit()               # the hook opens its own transactions on the shared session
    opened = {"event": "CONNECTION_UPDATE", "data": {"state": "open"}}
    await hook(client, channel.id, opened)
    await db.refresh(channel)
    assert channel.status == ChannelStatus.CONNECTED and channel.last_error is None
    assert ("whatsapp_catch_up", (channel.id,)) in [(name, args) for name, args, _ in
                                                     deps.tasks.calls]


async def test_a_switched_off_channel_is_acknowledged_and_ignored(
        client: ApiClient, world: World, db: AsyncSession, deps: FakeDeps) -> None:
    channel = await whatsapp_channel(db, deps, world, status=ChannelStatus.DISABLED)
    response = await hook(client, channel.id, upsert(mid="WAM2"))
    assert response.status_code == 200 and response.json()["stored"] == 0
    assert (await hook(client, channel.id, "not json at all")).status_code == 200
    assert await db.scalar(select(Message.id).where(Message.external_id == "WAM2")) is None
