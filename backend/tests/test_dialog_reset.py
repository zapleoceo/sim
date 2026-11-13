"""`/reset` in a chat wipes the dialog history and the persona's context (testing from scratch);
the owner's admin buttons wipe the chat or the passport alone."""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select

from sim.adapters.db.models import Client, Dialog, Job, Message
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.session import transaction
from sim.domain.enums import ClientStatus, Stage
from sim.services import jobkinds
from sim.services.dialog_reset import is_reset
from tests.conftest import OPERATOR, OWNER, SUPERADMIN, inbound, login
from tests.test_ingest import ingest


@pytest.mark.parametrize("text,expected", [
    ("/reset", True), ("  /RESET ", True), ("/reset@sim_bot", True),
    ("/reset please", False), ("reset", False), ("", False), (None, False),
])
def test_is_reset(text, expected):
    assert is_reset(text) is expected


async def seed_conversation(container, world) -> int:
    await ingest(container, world.channel.id, inbound("Привет", ext="1"))
    await ingest(container, world.channel.id, inbound("Расскажи о курсе", ext="2"))
    async with transaction(container.factory) as s:
        client = (await s.scalars(select(Client))).one()
        client.stage, client.needs, client.notes = Stage.INTERESTED, ["карьера"], "любит кофе"
        client.next_step, client.status = "дать ссылку", ClientStatus.NO_RESPONSE
        return (await s.scalars(select(Dialog))).one().id


async def test_reset_wipes_history_and_context_and_does_not_reply(container, world):
    dialog_id = await seed_conversation(container, world)
    res = await ingest(container, world.channel.id, inbound("/reset", ext="3"))
    assert res.reset and res.message is None and not res.reply_queued
    async with container.factory() as s:
        assert (await s.scalars(select(Message).where(Message.dialog_id == dialog_id))).all() == []
        client = (await s.scalars(select(Client))).one()
        assert (client.stage, client.status) == (Stage.FIRST_CONTACT, ClientStatus.ACTIVE)
        assert client.needs == [] and client.notes == "" and client.next_step == ""
        dialog = await s.get(Dialog, dialog_id)
        assert dialog.unread == 0 and dialog.last_message_text == ""
        pending = (await s.scalars(select(Job).where(
            Job.kind.in_((jobkinds.REPLY, jobkinds.FOLLOWUP)), Job.status == "pending"))).all()
        assert pending == []


async def test_the_next_message_starts_a_new_conversation(container, world):
    await seed_conversation(container, world)
    await ingest(container, world.channel.id, inbound("/reset", ext="3"))
    res = await ingest(container, world.channel.id, inbound("Привет ещё раз", ext="4"))
    assert res.reply_queued
    async with container.factory() as s:
        texts = [m.text for m in (await s.scalars(select(Message))).all()]
    assert texts == ["Привет ещё раз"]


async def test_reset_typed_on_the_connected_account_works_too(container, world):
    dialog_id = await seed_conversation(container, world)
    res = await ingest(container, world.channel.id, inbound("/reset", ext="5", from_me=True))
    assert res.reset
    async with container.factory() as s:
        assert (await s.scalars(select(Message).where(Message.dialog_id == dialog_id))).all() == []


async def test_reset_before_any_conversation_is_a_no_op(container, world):
    res = await ingest(container, world.channel.id, inbound("/reset", ext="1"))
    assert res.reset and res.dialog is None
    async with container.factory() as s:
        assert (await s.scalars(select(Message))).all() == []


# ---- the owner's buttons in the admin: «Очистить чат» and «Очистить паспорт» -------------------
PASSPORT_TEXT = "Анна, 30 лет, любит кофе"


async def seed_passport(container) -> tuple[int, int]:
    """A passport, collected data and a pending passport update. Returns (client id, dialog id)."""
    async with transaction(container.factory) as s:
        client = (await s.scalars(select(Client))).one()
        dialog = (await s.scalars(select(Dialog))).one()
        client.passport, client.passport_updated_at = PASSPORT_TEXT, datetime(2026, 11, 5, 12, 0)
        await JobRepo(s).upsert_pending(jobkinds.PASSPORT, {"dialog_id": dialog.id},
                                        datetime(2026, 11, 5, 12, 5), jobkinds.passport_key(dialog.id))
        return client.id, dialog.id


async def pending_jobs(container, *kinds: str) -> list[Job]:
    async with container.factory() as s:
        return list((await s.scalars(select(Job).where(
            Job.kind.in_(kinds), Job.status == "pending"))).all())


@pytest.mark.parametrize("admin", [OWNER, SUPERADMIN])
async def test_owner_clears_the_chat_but_keeps_the_passport(api, container, world, admin):
    dialog_id = await seed_conversation(container, world)
    await seed_passport(container)
    h = {"X-CSRF-Token": await login(api, *admin)}
    resp = await api.delete(f"/api/dialogs/{dialog_id}/messages", headers=h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted_messages"] == 2 and body["messages"] == []
    assert body["dialog"]["id"] == dialog_id and body["client"]["passport"] == PASSPORT_TEXT
    async with container.factory() as s:
        assert (await s.scalars(select(Message).where(Message.dialog_id == dialog_id))).all() == []
        dialog = await s.get(Dialog, dialog_id)
        assert (dialog.unread, dialog.last_message_text, dialog.last_message_at) == (0, "", None)
        client = (await s.scalars(select(Client))).one()
        assert client.passport == PASSPORT_TEXT and client.notes == "любит кофе"
        assert client.stage is Stage.INTERESTED      # the funnel position is not the chat
    assert await pending_jobs(container, jobkinds.REPLY, jobkinds.FOLLOWUP, jobkinds.PASSPORT) == []


async def test_after_clearing_the_chat_the_next_message_starts_without_the_past(api, container, world):
    dialog_id = await seed_conversation(container, world)
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    assert (await api.delete(f"/api/dialogs/{dialog_id}/messages", headers=h)).status_code == 200
    res = await ingest(container, world.channel.id, inbound("Привет ещё раз", ext="9"))
    assert res.reply_queued
    async with container.factory() as s:
        rows = (await s.scalars(select(Message))).all()
        assert [(m.dialog_id, m.text) for m in rows] == [(dialog_id, "Привет ещё раз")]


async def test_owner_clears_the_passport_but_keeps_the_client_and_the_chat(api, container, world):
    dialog_id = await seed_conversation(container, world)
    client_id, _ = await seed_passport(container)
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    resp = await api.delete(f"/api/clients/{client_id}/passport", headers=h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == client_id and body["passport"] == "" and body["passport_updated_at"] is None
    assert body["needs"] == [] and body["notes"] == "" and body["next_step"] == ""
    assert body["channels"] == [{"kind": "telegram", "dialog_id": dialog_id}]
    async with container.factory() as s:
        client = await s.get(Client, client_id)
        assert client is not None and client.passport == "" and client.next_step_at is None
        assert (client.stage, client.status) == (Stage.INTERESTED, ClientStatus.NO_RESPONSE)
        texts = [m.text for m in (await s.scalars(select(Message))).all()]
        assert texts == ["Привет", "Расскажи о курсе"]
    assert await pending_jobs(container, jobkinds.PASSPORT) == []
    assert len(await pending_jobs(container, jobkinds.REPLY)) == 1   # the conversation goes on


async def test_an_operator_gets_403_and_nothing_is_wiped(api, container, world):
    dialog_id = await seed_conversation(container, world)
    client_id, _ = await seed_passport(container)
    h = {"X-CSRF-Token": await login(api, *OPERATOR)}
    chat = await api.delete(f"/api/dialogs/{dialog_id}/messages", headers=h)
    passport = await api.delete(f"/api/clients/{client_id}/passport", headers=h)
    assert (chat.status_code, passport.status_code) == (403, 403)
    assert chat.json()["detail"] == "Owner role required"
    async with container.factory() as s:
        assert len((await s.scalars(select(Message))).all()) == 2
        assert (await s.get(Client, client_id)).passport == PASSPORT_TEXT


async def test_clearing_needs_the_csrf_token_and_an_existing_target(api, container, world):
    dialog_id = await seed_conversation(container, world)
    client_id, _ = await seed_passport(container)
    csrf = await login(api, *OWNER)
    assert (await api.delete(f"/api/dialogs/{dialog_id}/messages")).status_code == 403
    assert (await api.delete(f"/api/clients/{client_id}/passport")).status_code == 403
    h = {"X-CSRF-Token": csrf}
    assert (await api.delete("/api/dialogs/999999/messages", headers=h)).status_code == 404
    assert (await api.delete("/api/clients/999999/passport", headers=h)).status_code == 404
    async with container.factory() as s:
        assert len((await s.scalars(select(Message))).all()) == 2
        assert (await s.get(Client, client_id)).passport == PASSPORT_TEXT
