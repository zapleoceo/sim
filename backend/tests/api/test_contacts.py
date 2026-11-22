"""«Запланировать» in a chat (docs/initiative.md, docs/api.md): the manager plans a message or a
call, lists them with her own calls, cancels one that has not started; and the chat's
«Звонит сама» switch."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, Persona, PlannedContact
from sim.domain.enums import ContactKind, ContactSource, ContactStatus
from tests.api.helpers import ApiClient, World


def contacts(world: World, path: str = "") -> str:
    return f"/api/chats/{world.chat_id}/contacts{path}"


async def test_the_manager_plans_a_message_and_sees_it(api: ApiClient, world: World) -> None:
    assert (await api.get(contacts(world))).json() == {"items": []}
    created = await api.post(contacts(world), json={
        "kind": "message", "topic": "  спросить   про отпуск ",
        "due_at": "2026-11-21T20:00:00Z"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["kind"], body["source"], body["status"]) == ("message", "manager", "planned")
    assert body["topic"] == "спросить про отпуск" and body["chat_id"] == world.chat_id
    assert datetime.fromisoformat(body["due_at"]) == datetime(2026, 11, 21, 20, tzinfo=UTC)
    assert body["created_by"].startswith("operator:") and body["result"] == ""
    assert body["finished_at"] is None and body["call_id"] is None
    assert (await api.get(contacts(world))).json()["items"] == [body]
    # A time without a zone is UTC.
    naive = await api.post(contacts(world), json={"kind": "message", "topic": "ещё",
                                                  "due_at": "2026-11-21T21:00:00"})
    assert datetime.fromisoformat(naive.json()["due_at"]) == datetime(2026, 11, 21, 21,
                                                                      tzinfo=UTC)


async def test_bad_plans_are_refused(api: ApiClient, world: World) -> None:
    past = await api.post(contacts(world), json={"kind": "message", "topic": "привет",
                                                 "due_at": "2026-11-21T18:00:00Z"})
    assert past.status_code == 400 and "время уже прошло" in past.json()["error"]
    no_model = await api.post(contacts(world), json={"kind": "call", "topic": "созвониться",
                                                     "due_at": "2026-11-21T20:00:00Z"})
    assert no_model.status_code == 400
    assert no_model.json()["error"] == "Не выбрана модель звонка"
    for payload in ({"kind": "letter", "topic": "x", "due_at": "2026-11-21T20:00:00Z"},
                    {"kind": "message", "topic": "", "due_at": "2026-11-21T20:00:00Z"},
                    {"kind": "message", "topic": "x"}):
        assert (await api.post(contacts(world), json=payload)).status_code == 400, payload
    assert (await api.get("/api/chats/999999/contacts")).status_code == 404
    unknown = await api.post("/api/chats/999999/contacts", json={
        "kind": "message", "topic": "x", "due_at": "2026-11-21T20:00:00Z"})
    assert unknown.status_code == 404


async def test_a_call_with_working_calls(api: ApiClient, world: World,
                                         db: AsyncSession) -> None:
    persona = await db.get(Persona, world.persona_id)
    assert persona is not None
    persona.agents = {**persona.agents, "live": {"model": "google/live"}}
    await db.commit()
    created = await api.post(contacts(world), json={"kind": "call", "topic": "созвониться",
                                                    "due_at": "2026-11-21T19:00:00Z"})
    assert created.status_code == 201, created.text
    assert (created.json()["kind"], created.json()["source"]) == ("call", "manager")


async def test_cancel_only_what_has_not_started(api: ApiClient, world: World,
                                                db: AsyncSession) -> None:
    created = (await api.post(contacts(world), json={
        "kind": "message", "topic": "привет", "due_at": "2026-11-21T20:00:00Z"})).json()
    cancelled = await api.post(contacts(world, f"/{created['id']}/cancel"))
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled" and cancelled.json()["finished_at"]
    again = await api.post(contacts(world, f"/{created['id']}/cancel"))
    assert again.status_code == 409 and "ещё не началось" in again.json()["error"]

    done = PlannedContact(chat_id=world.chat_id, kind=ContactKind.MESSAGE,
                          source=ContactSource.PERSONA, status=ContactStatus.DONE,
                          topic="привет", due_at=datetime(2026, 11, 21, 18, tzinfo=UTC),
                          created_by="persona", result="она написала первой")
    db.add(done)
    await db.commit()
    done_id = done.id               # a refused request rolls back and expires the session
    assert (await api.post(contacts(world, f"/{done_id}/cancel"))).status_code == 409
    assert (await api.post(contacts(world, "/999999/cancel"))).status_code == 404
    other = await api.post(f"/api/chats/{world.chat_id + 1}/contacts/{done_id}/cancel")
    assert other.status_code in (400, 404)              # never another chat's contact


async def test_calls_out_switch_of_one_chat(api: ApiClient, world: World,
                                            db: AsyncSession) -> None:
    listed = (await api.get("/api/chats")).json()["items"][0]
    assert listed["calls_out"] is True                  # her own tool: on by default (0011)
    first_off = await api.post(f"/api/chats/{world.chat_id}/calls-out", json={"enabled": False})
    assert first_off.json()["calls_out"] is False
    response = await api.post(f"/api/chats/{world.chat_id}/calls-out", json={"enabled": True})
    assert response.status_code == 200
    assert response.json()["calls_out"] is True
    chat = await db.get(Chat, world.chat_id)
    assert chat is not None and chat.calls_out is True
    off = await api.post(f"/api/chats/{world.chat_id}/calls-out", json={"enabled": False})
    assert off.json()["calls_out"] is False
    assert (await api.post("/api/chats/999999/calls-out",
                           json={"enabled": True})).status_code == 404
