"""Leads: list with filters (persona, stage, search), card with chats and the passport, edits
that are journaled in every chat of the client, passport fields locked by the operator."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Channel, Chat, ChatEvent, Client, Persona
from sim.domain.enums import ChannelKind, EventKind
from tests.api.fakes import FakeDeps
from tests.api.helpers import ApiClient, World


async def test_list_clients_and_search(api: ApiClient, world: World, db: AsyncSession) -> None:
    db.add(Client(persona_id=world.persona_id, name="Оля", username="olya"))
    await db.commit()
    names = [c["name"] for c in (await api.get("/api/clients")).json()["items"]]
    assert sorted(names) == ["Дима Петров", "Оля"]
    found = (await api.get("/api/clients", params={"q": "OLY"})).json()["items"]
    assert [c["name"] for c in found] == ["Оля"]
    assert found[0]["ai_enabled"] is False
    assert found[0]["passport"]["stage"] == "new" and found[0]["passport"]["text"] == ""
    assert (found[0]["chats_count"], found[0]["messages_count"]) == (0, 0)
    by_name = {c["name"]: c for c in (await api.get("/api/clients")).json()["items"]}
    dima = by_name["Дима Петров"]
    assert (dima["chats_count"], dima["messages_count"]) == (1, 1)
    assert dima["persona"] == {"id": world.persona_id, "name": "Марина", "avatar_media_id": None}


async def test_filters_by_persona_and_stage(api: ApiClient, world: World,
                                            db: AsyncSession) -> None:
    other = Persona(name="Оля-персонаж")
    db.add(other)
    await db.flush()
    db.add_all([
        Client(persona_id=other.id, name="Петя", passport={"stage": "hot", "temperature": 80}),
        Client(persona_id=world.persona_id, name="Вася", passport={"stage": "warm"}),
    ])
    await db.commit()

    def names(response: Any) -> list[str]:
        return sorted(c["name"] for c in response.json()["items"])

    assert names(await api.get("/api/clients", params={"persona_id": other.id})) == ["Петя"]
    assert names(await api.get("/api/clients", params={"stage": "hot"})) == ["Петя"]
    assert names(await api.get("/api/clients", params={"stage": "new"})) == ["Дима Петров"]
    assert names(await api.get("/api/clients", params={
        "persona_id": world.persona_id, "stage": "warm"})) == ["Вася"]
    assert (await api.get("/api/clients", params={"stage": "boss"})).status_code == 400


async def test_client_card_with_chats_and_404(api: ApiClient, world: World) -> None:
    response = await api.get(f"/api/clients/{world.client_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Дима Петров" and body["persona"]["name"] == "Марина"
    assert [c["id"] for c in body["chats"]] == [world.chat_id]
    assert body["chats"][0]["last_message"]["text"] == "привет"
    assert body["chats"][0]["persona"]["id"] == world.persona_id
    assert (await api.get("/api/clients/999999")).status_code == 404


async def test_patch_client_writes_events_in_every_chat(api: ApiClient, world: World,
                                                        db: AsyncSession) -> None:
    second = Channel(persona_id=world.persona_id, kind=ChannelKind.TELEGRAM_USER, title="2")
    db.add(second)
    await db.flush()
    other_chat = Chat(persona_id=world.persona_id, client_id=world.client_id,
                      channel_id=second.id)
    db.add(other_chat)
    await db.commit()

    response = await api.patch(f"/api/clients/{world.client_id}", json={
        "name": "Дмитрий", "phone": "+380501112233", "timezone": "Europe/Kyiv",
        "ai_enabled": False, "passport": {"city": " Киев ", "text": "Кто он: Дима"}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["name"], body["phone"], body["timezone"], body["ai_enabled"]) == (
        "Дмитрий", "+380501112233", "Europe/Kyiv", False)
    assert (body["passport"]["city"], body["passport"]["text"]) == ("Киев", "Кто он: Дима")

    for chat_id in (world.chat_id, other_chat.id):
        events = (await db.scalars(select(ChatEvent).where(
            ChatEvent.chat_id == chat_id,
            ChatEvent.kind.in_((EventKind.CLIENT_UPDATED, EventKind.AI_TOGGLED)),
        ).order_by(ChatEvent.id))).all()
        assert [(e.kind, e.data) for e in events] == [
            (EventKind.CLIENT_UPDATED, {"fields": ["name", "passport", "phone", "timezone"]}),
            (EventKind.AI_TOGGLED, {"enabled": False, "scope": "client"}),
        ]


async def test_passport_edit_locks_the_structured_fields(api: ApiClient, world: World,
                                                         db: AsyncSession) -> None:
    url = f"/api/clients/{world.client_id}"
    edited = await api.patch(url, json={"passport": {
        "stage": "hot", "temperature": 70, "age": 34, "text": "Жизнь: двое детей"}})
    assert edited.status_code == 200, edited.text
    passport = edited.json()["passport"]
    assert (passport["stage"], passport["temperature"], passport["age"]) == ("hot", 70, 34)
    assert sorted(passport["locked_fields"]) == ["age", "stage", "temperature"]
    assert passport["updated_at"]
    unlocked = await api.patch(url, json={"passport": {"locked_fields": ["stage"]}})
    assert unlocked.json()["passport"]["locked_fields"] == ["stage"]
    client = await db.get(Client, world.client_id)
    assert client is not None and client.passport["locked_fields"] == ["stage"]


async def test_patch_without_changes_writes_nothing(api: ApiClient, world: World,
                                                    db: AsyncSession) -> None:
    response = await api.patch(f"/api/clients/{world.client_id}",
                               json={"name": "Дима Петров", "ai_enabled": True, "passport": {}})
    assert response.status_code == 200
    events = (await db.scalars(select(ChatEvent).where(
        ChatEvent.kind == EventKind.CLIENT_UPDATED))).all()
    assert events == []


async def test_patch_client_validation_and_404(api: ApiClient, world: World) -> None:
    url = f"/api/clients/{world.client_id}"
    assert (await api.patch(url, json={"timezone": "Nowhere/X"})).status_code == 400
    for passport in ({"timezone": "Nowhere/X"}, {"temperature": 101}, {"stage": "boss"},
                     {"age": 0}):
        assert (await api.patch(url, json={"passport": passport})).status_code == 400, passport
    assert (await api.patch("/api/clients/999999", json={"name": "x"})).status_code == 404


async def test_switching_the_client_ai_on_answers_waiting_chats(
        api: ApiClient, world: World, db: AsyncSession, deps: FakeDeps) -> None:
    client = await db.get(Client, world.client_id)
    chat = await db.get(Chat, world.chat_id)
    assert client is not None and chat is not None
    client.ai_enabled = False
    chat.needs_operator = False
    await db.commit()
    response = await api.patch(f"/api/clients/{world.client_id}", json={"ai_enabled": True})
    assert response.status_code == 200
    assert deps.tasks.calls == [("reply", (world.chat_id, 0), False)]
