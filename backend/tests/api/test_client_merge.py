"""One person in many messengers (docs/leads.md): «возможно, один человек» suggestions, «это
разные люди», the operator's merge of two clients and the client's journal over every chat."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import (
    Channel,
    Chat,
    ChatDay,
    ChatEvent,
    Client,
    ClientDistinct,
    ClientIdentity,
    Message,
)
from sim.domain.enums import (
    Author,
    ChannelKind,
    DaySource,
    Direction,
    EventKind,
    MessageKind,
    MessageStatus,
)
from sim.events import append_event
from tests.api.helpers import ApiClient, World


@dataclass(frozen=True)
class WhatsApp:
    client_id: int
    chat_id: int
    event_id: int


async def whatsapp_twin(db: AsyncSession, world: World, *, phone: str = "+380994811889",
                        text: str = "привет из ватсапа") -> WhatsApp:
    """The world's client (Telegram, «Дима Петров») writes from WhatsApp too: a second client,
    as Inbox creates it, with a chat, a message and a day of memory."""
    telegram = await db.get(Client, world.client_id)
    assert telegram is not None
    telegram.phone = phone
    telegram.passport = {"text": "Работает в SIM", "city": "Киев", "temperature": 30,
                         "locked_fields": ["city"]}
    db.add(ClientIdentity(client_id=world.client_id, channel_kind=ChannelKind.TELEGRAM_USER,
                          external_id="169510539"))
    channel = Channel(persona_id=world.persona_id, kind=ChannelKind.WHATSAPP, title="WA")
    client = Client(persona_id=world.persona_id, name="Zaporozhets Dmitry", notes="из WhatsApp",
                    passport={"text": "Есть дочь", "age": 35, "temperature": 70})
    db.add_all([channel, client])
    await db.flush()
    db.add(ClientIdentity(client_id=client.id, channel_kind=ChannelKind.WHATSAPP,
                          external_id="380994811889@s.whatsapp.net"))
    chat = Chat(persona_id=world.persona_id, client_id=client.id, channel_id=channel.id,
                external_thread_id="380994811889@s.whatsapp.net")
    db.add(chat)
    await db.flush()
    message = Message(chat_id=chat.id, direction=Direction.IN, author=Author.CLIENT,
                      kind=MessageKind.TEXT, text=text, status=MessageStatus.RECEIVED)
    db.add(message)
    await db.flush()
    event = await append_event(db, chat_id=chat.id, kind=EventKind.MESSAGE_IN, actor="client",
                               message_id=message.id)
    db.add(ChatDay(chat_id=chat.id, day=event.created_at.date(), summary="Болтали о работе",
                   source=DaySource.MODEL))
    await db.commit()
    return WhatsApp(client_id=client.id, chat_id=chat.id, event_id=event.id)


async def test_suggests_one_person_by_phone_and_hides_a_distinct_pair(
        api: ApiClient, world: World, db: AsyncSession) -> None:
    twin = await whatsapp_twin(db, world)
    [pair] = (await api.get("/api/clients/matches")).json()["items"]
    assert [c["id"] for c in pair["clients"]] == [world.client_id, twin.client_id]
    assert pair["clients"][1]["messengers"] == ["whatsapp"]
    assert pair["clients"][0]["persona"]["name"] == "Марина"
    # «Дима» and «Dmitry» are one first name: told alongside the phone, never alone.
    assert pair["reasons"] == [
        {"kind": "phone", "text": "один телефон …994811889"},
        {"kind": "name", "text": "похожие имена: «Дима Петров» и «Zaporozhets Dmitry»"}]
    mine = (await api.get(f"/api/clients/{twin.client_id}/matches")).json()["items"]
    assert [[c["id"] for c in p["clients"]] for p in mine] == [[world.client_id, twin.client_id]]

    response = await api.post(f"/api/clients/{twin.client_id}/distinct",
                              json={"other_id": world.client_id})
    assert response.status_code == 204
    again = await api.post(f"/api/clients/{world.client_id}/distinct",
                           json={"other_id": twin.client_id})
    assert again.status_code == 204                            # stored once, smaller id first
    rows = (await db.execute(select(ClientDistinct.client_id, ClientDistinct.other_id))).all()
    assert rows == [(world.client_id, twin.client_id)]
    assert (await api.get("/api/clients/matches")).json()["items"] == []
    assert (await api.post(f"/api/clients/{twin.client_id}/distinct",
                           json={"other_id": twin.client_id})).status_code == 400
    assert (await api.get("/api/clients/999999/matches")).status_code == 404


async def test_a_mention_in_the_messages_is_a_reason(api: ApiClient, world: World,
                                                     db: AsyncSession) -> None:
    await whatsapp_twin(db, world, phone="", text="это я, мой телеграм @dima_p")
    [pair] = (await api.get("/api/clients/matches")).json()["items"]
    assert [r["kind"] for r in pair["reasons"]] == ["mention", "name"]
    assert pair["reasons"][0]["text"] == "в переписке назван @dima_p"


async def test_merge_moves_identities_chats_and_memory_and_combines_the_profile(
        api: ApiClient, world: World, db: AsyncSession) -> None:
    twin = await whatsapp_twin(db, world)
    response = await api.post(f"/api/clients/{world.client_id}/merge",
                              json={"other_id": twin.client_id})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == world.client_id and body["name"] == "Дима Петров"
    assert body["messengers"] == ["telegram_user", "whatsapp"]
    assert sorted(c["id"] for c in body["chats"]) == sorted([world.chat_id, twin.chat_id])
    assert body["notes"] == "из WhatsApp" and body["phone"] == "+380994811889"
    passport = body["passport"]
    assert passport["text"] == "Работает в SIM\n\nЕсть дочь"
    assert (passport["city"], passport["age"], passport["temperature"]) == ("Киев", 35, 70)
    assert passport["locked_fields"] == ["city"]

    db.expire_all()
    assert await db.get(Client, twin.client_id) is None
    chat = await db.get(Chat, twin.chat_id)
    assert chat is not None and chat.client_id == world.client_id
    kinds = set(await db.scalars(select(ClientIdentity.channel_kind)
                                 .where(ClientIdentity.client_id == world.client_id)))
    assert kinds == {ChannelKind.TELEGRAM_USER, ChannelKind.WHATSAPP}
    # Messages, journal and the day diary stay with the chat: nothing is lost.
    assert await db.scalar(select(func.count()).select_from(ChatDay)
                           .where(ChatDay.chat_id == twin.chat_id)) == 1
    assert await db.scalar(select(func.count()).select_from(Message)
                           .where(Message.chat_id == twin.chat_id)) == 1
    merged = list(await db.scalars(select(ChatEvent).where(
        ChatEvent.kind == EventKind.CLIENT_MERGED).order_by(ChatEvent.chat_id)))
    assert [e.chat_id for e in merged] == sorted([world.chat_id, twin.chat_id])
    assert all(not e.in_context for e in merged)
    assert [e.data["moved"] for e in merged] == [False, True]   # the world's chat is older
    assert merged[0].data == {"from_client_id": twin.client_id, "from_name": "Zaporozhets Dmitry",
                              "into_client_id": world.client_id, "moved": False}
    assert (await api.get("/api/clients/matches")).json()["items"] == []


async def test_merge_is_refused_for_one_messenger_the_same_client_and_unknown_ones(
        api: ApiClient, world: World, db: AsyncSession) -> None:
    twin = await whatsapp_twin(db, world)
    other_telegram = Client(persona_id=world.persona_id, name="Второй Дима")
    db.add(other_telegram)
    await db.flush()
    other_id = other_telegram.id
    db.add(ClientIdentity(client_id=other_id, channel_kind=ChannelKind.TELEGRAM_USER,
                          external_id="42"))
    await db.commit()
    refused = await api.post(f"/api/clients/{world.client_id}/merge",
                             json={"other_id": other_id})
    assert refused.status_code == 409 and "Telegram" in refused.text
    same = await api.post(f"/api/clients/{twin.client_id}/merge",
                          json={"other_id": twin.client_id})
    assert same.status_code == 409
    missing = await api.post(f"/api/clients/{twin.client_id}/merge", json={"other_id": 999999})
    assert missing.status_code == 404
    assert (await api.post(f"/api/clients/{twin.client_id}/merge",
                           json={"other_id": 0})).status_code == 400
    db.expire_all()
    assert await db.get(Client, other_id) is not None


async def test_client_timeline_is_one_stream_over_every_chat(api: ApiClient, world: World,
                                                             db: AsyncSession) -> None:
    twin = await whatsapp_twin(db, world)
    assert (await api.post(f"/api/clients/{world.client_id}/merge",
                           json={"other_id": twin.client_id})).status_code == 200
    items = (await api.get(f"/api/clients/{world.client_id}/timeline")).json()["items"]
    ids = [i["id"] for i in items]
    assert ids == sorted(ids, reverse=True)
    by_event = {i["id"]: i for i in items}
    assert by_event[twin.event_id]["channel"]["kind"] == "whatsapp"
    assert by_event[twin.event_id]["chat_id"] == twin.chat_id
    assert by_event[world.event_id]["channel"]["kind"] == "telegram_user"
    assert by_event[twin.event_id]["message"]["text"] == "привет из ватсапа"
    page = (await api.get(f"/api/clients/{world.client_id}/timeline",
                          params={"before_id": twin.event_id, "limit": 1})).json()["items"]
    assert [i["id"] for i in page] == [world.event_id]
    assert (await api.get("/api/clients/999999/timeline")).status_code == 404
