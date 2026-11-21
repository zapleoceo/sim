"""Clients: list, card with chats, edits that are journaled in every chat of the client."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Channel, Chat, ChatEvent, Client
from sim.domain.enums import ChannelKind, EventKind
from tests.api.helpers import ApiClient, World


async def test_list_clients_and_search(api: ApiClient, world: World, db: AsyncSession) -> None:
    db.add(Client(persona_id=world.persona_id, name="Оля", username="olya"))
    await db.commit()
    names = [c["name"] for c in (await api.get("/api/clients")).json()["items"]]
    assert sorted(names) == ["Дима Петров", "Оля"]
    found = (await api.get("/api/clients", params={"q": "OLY"})).json()["items"]
    assert [c["name"] for c in found] == ["Оля"]
    assert found[0]["ai_enabled"] is False
    assert found[0]["memory"] == []
    assert found[0]["chats_count"] == 0
    by_name = {c["name"]: c for c in (await api.get("/api/clients")).json()["items"]}
    assert by_name["Дима Петров"]["chats_count"] == 1


async def test_client_card_with_chats_and_404(api: ApiClient, world: World) -> None:
    response = await api.get(f"/api/clients/{world.client_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Дима Петров"
    assert [c["id"] for c in body["chats"]] == [world.chat_id]
    assert body["chats"][0]["last_message"]["text"] == "привет"
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
        "ai_enabled": False, "memory": [{"fact": "живёт в Киеве"}]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["name"], body["phone"], body["timezone"], body["ai_enabled"]) == (
        "Дмитрий", "+380501112233", "Europe/Kyiv", False)
    [fact] = body["memory"]
    assert fact["fact"] == "живёт в Киеве" and fact["event_id"] is None and fact["at"]

    for chat_id in (world.chat_id, other_chat.id):
        events = (await db.scalars(select(ChatEvent).where(
            ChatEvent.chat_id == chat_id,
            ChatEvent.kind.in_((EventKind.CLIENT_UPDATED, EventKind.AI_TOGGLED)),
        ).order_by(ChatEvent.id))).all()
        assert [(e.kind, e.data) for e in events] == [
            (EventKind.CLIENT_UPDATED, {"fields": ["memory", "name", "phone", "timezone"]}),
            (EventKind.AI_TOGGLED, {"enabled": False, "scope": "client"}),
        ]


async def test_patch_without_changes_writes_nothing(api: ApiClient, world: World,
                                                    db: AsyncSession) -> None:
    response = await api.patch(f"/api/clients/{world.client_id}",
                               json={"name": "Дима Петров", "ai_enabled": True})
    assert response.status_code == 200
    events = (await db.scalars(select(ChatEvent).where(
        ChatEvent.kind == EventKind.CLIENT_UPDATED))).all()
    assert events == []


async def test_patch_client_validation_and_404(api: ApiClient, world: World) -> None:
    bad_tz = await api.patch(f"/api/clients/{world.client_id}", json={"timezone": "Nowhere/X"})
    assert bad_tz.status_code == 400
    bad_memory = await api.patch(f"/api/clients/{world.client_id}", json={"memory": [{}]})
    assert bad_memory.status_code == 400
    assert (await api.patch("/api/clients/999999", json={"name": "x"})).status_code == 404
