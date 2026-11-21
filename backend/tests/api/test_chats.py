"""Chats: list, detail, timeline, operator actions, reply-run details."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import (
    Channel,
    Chat,
    ChatEvent,
    LlmCall,
    MediaAsset,
    Message,
    Persona,
    ReplyRun,
)
from sim.domain.enums import (
    Author,
    ChannelKind,
    EventKind,
    LlmPurpose,
    MediaSource,
    MessageKind,
    MessageStatus,
    RunStatus,
)
from tests.api.fakes import PNG, FakeDeps
from tests.api.helpers import ApiClient, World


async def test_list_chats_with_last_message_and_search(api: ApiClient, world: World) -> None:
    response = await api.get("/api/chats")
    assert response.status_code == 200
    [row] = response.json()["items"]
    assert row["id"] == world.chat_id
    assert row["client"] == {"id": world.client_id, "name": "Дима Петров",
                             "username": "dima_p", "ai_enabled": True}
    assert row["unread_count"] == 3
    assert row["needs_operator"] is True
    assert row["last_message"] == {"kind": "text", "direction": "in", "author": "client",
                                   "text": "привет"}

    # Cyrillic case folding depends on the server locale, so only ASCII case is mixed here.
    for q in ("Петров", "@dima_p", "DIMA"):
        found = (await api.get("/api/chats", params={"q": q})).json()["items"]
        assert [r["id"] for r in found] == [world.chat_id], q
    assert (await api.get("/api/chats", params={"q": "nobody"})).json()["items"] == []


async def test_list_chats_shows_and_filters_the_persona(api: ApiClient, world: World,
                                                        db: AsyncSession) -> None:
    other = Persona(name="Оля")
    db.add(other)
    await db.flush()
    channel = Channel(persona_id=other.id, kind=ChannelKind.TELEGRAM_USER, title="Оля")
    db.add(channel)
    await db.flush()
    # The same client wrote to Оля's account too: a second chat, answered by Оля.
    db.add(Chat(persona_id=other.id, client_id=world.client_id, channel_id=channel.id))
    await db.commit()
    rows = (await api.get("/api/chats")).json()["items"]
    assert sorted(r["persona"]["name"] for r in rows) == ["Марина", "Оля"]
    mine = (await api.get("/api/chats", params={"persona_id": world.persona_id})).json()
    assert [r["id"] for r in mine["items"]] == [world.chat_id]
    theirs = (await api.get("/api/chats", params={"persona_id": other.id})).json()["items"]
    assert [r["persona"]["id"] for r in theirs] == [other.id]


async def test_list_chats_before_cursor(api: ApiClient, world: World) -> None:
    row = (await api.get("/api/chats")).json()["items"][0]
    older = await api.get("/api/chats", params={"before": row["last_message_at"]})
    assert older.status_code == 200
    assert older.json()["items"] == []


async def test_chat_detail_and_404(api: ApiClient, world: World) -> None:
    response = await api.get(f"/api/chats/{world.chat_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == world.chat_id
    assert body["client"]["phone"] is None
    assert body["client"]["passport"]["text"] == ""
    assert body["persona"] == {"id": world.persona_id, "name": "Марина", "avatar_media_id": None}
    missing = await api.get("/api/chats/999999")
    assert missing.status_code == 404
    assert "error" in missing.json()


async def test_timeline_newest_first_with_message(api: ApiClient, world: World,
                                                  db: AsyncSession) -> None:
    note = ChatEvent(chat_id=world.chat_id, kind=EventKind.OPERATOR_NOTE, actor="operator:1",
                     data={"text": "она в отпуске"})
    db.add(note)
    await db.commit()

    items = (await api.get(f"/api/chats/{world.chat_id}/timeline")).json()["items"]
    assert [i["id"] for i in items] == [note.id, world.event_id]
    first, second = items
    assert first["kind"] == "operator_note" and "message" not in first
    assert first["data"] == {"text": "она в отпуске"}
    assert second["message"]["id"] == world.message_id
    assert second["message"]["text"] == "привет"
    assert second["in_context"] is True

    page = (await api.get(f"/api/chats/{world.chat_id}/timeline",
                          params={"before_id": note.id, "limit": 1})).json()["items"]
    assert [i["id"] for i in page] == [world.event_id]
    assert (await api.get("/api/chats/999999/timeline")).status_code == 404


async def test_operator_text_is_queued_after_commit(api: ApiClient, world: World,
                                                    db: AsyncSession, deps: FakeDeps) -> None:
    response = await api.post(f"/api/chats/{world.chat_id}/messages", json={"text": " привет "})
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["message"]["text"] == "привет"
    assert item["message"]["author"] == "operator"
    assert item["message"]["status"] == "pending"

    message = await db.get(Message, item["message"]["id"])
    assert message is not None
    assert (message.author, message.kind, message.status) == (
        Author.OPERATOR, MessageKind.TEXT, MessageStatus.PENDING)
    # Deferred exactly once, with no transaction left open: the row was already committed.
    assert deps.tasks.calls == [("send", (message.id, world.chat_id), False)]


async def test_operator_file_goes_to_library_and_is_queued(api: ApiClient, world: World,
                                                           db: AsyncSession,
                                                           deps: FakeDeps) -> None:
    response = await api.post(f"/api/chats/{world.chat_id}/messages",
                              files={"file": ("selfie.png", PNG, "image/png")},
                              data={"caption": "это я"})
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["message"]["kind"] == "photo"
    assert item["message"]["media"]["url"].endswith("/file")

    media = await db.get(MediaAsset, item["message"]["media"]["id"])
    assert media is not None
    assert (media.source, media.is_reference, media.persona_id) == (
        MediaSource.LIBRARY, False, world.persona_id)
    assert deps.storage.files[media.storage_key] == PNG
    assert [c[0] for c in deps.tasks.calls] == ["send"]
    assert deps.tasks.calls[0][2] is False


async def test_operator_message_validation(api: ApiClient, world: World,
                                           deps: FakeDeps) -> None:
    empty = await api.post(f"/api/chats/{world.chat_id}/messages", json={"text": ""})
    assert empty.status_code == 400
    assert "error" in empty.json()
    no_file = await api.post(f"/api/chats/{world.chat_id}/messages",
                             files={"other": ("a.txt", b"x", "text/plain")})
    assert no_file.status_code == 400
    missing = await api.post("/api/chats/999999/messages", json={"text": "hi"})
    assert missing.status_code == 404
    assert deps.tasks.calls == []


async def test_ai_toggle_writes_event(api: ApiClient, world: World, db: AsyncSession) -> None:
    response = await api.post(f"/api/chats/{world.chat_id}/ai", json={"enabled": False})
    assert response.status_code == 200
    assert response.json()["ai_enabled"] is False
    events = (await db.scalars(select(ChatEvent).where(
        ChatEvent.chat_id == world.chat_id, ChatEvent.kind == EventKind.AI_TOGGLED))).all()
    assert [(e.data, e.in_context, e.actor.startswith("operator:")) for e in events] == [
        ({"enabled": False, "scope": "chat"}, False, True)]
    assert (await api.post("/api/chats/999999/ai", json={"enabled": True})).status_code == 404


async def test_answer_at_once_switch_of_one_chat(api: ApiClient, world: World,
                                                 db: AsyncSession) -> None:
    listed = (await api.get("/api/chats")).json()["items"][0]
    assert listed["attention_instant"] is False
    response = await api.post(f"/api/chats/{world.chat_id}/attention-instant",
                              json={"enabled": True})
    assert response.status_code == 200
    assert response.json()["attention_instant"] is True
    chat = await db.get(Chat, world.chat_id)
    assert chat is not None and chat.attention_instant is True
    assert (await api.post("/api/chats/999999/attention-instant",
                           json={"enabled": True})).status_code == 404


async def test_switching_ai_on_answers_what_arrived_while_it_was_off(
        api: ApiClient, world: World, db: AsyncSession, deps: FakeDeps) -> None:
    chat = await db.get(Chat, world.chat_id)
    assert chat is not None
    chat.ai_enabled = False
    chat.needs_operator = False
    await db.commit()
    await api.post(f"/api/chats/{world.chat_id}/ai", json={"enabled": True})
    # The world's client message is unanswered: a phone check, deferred after the commit.
    [(name, args, in_transaction)] = deps.tasks.calls
    assert (name, args[0], in_transaction) == ("phone_check", world.persona_id, False)


async def test_switching_ai_off_or_again_on_wakes_nothing(
        api: ApiClient, world: World, db: AsyncSession, deps: FakeDeps) -> None:
    chat = await db.get(Chat, world.chat_id)
    assert chat is not None
    chat.needs_operator = False
    await db.commit()
    await api.post(f"/api/chats/{world.chat_id}/ai", json={"enabled": True})   # already on
    await api.post(f"/api/chats/{world.chat_id}/ai", json={"enabled": False})
    assert deps.tasks.calls == []


async def test_a_chat_waiting_for_an_operator_is_not_woken(
        api: ApiClient, world: World, db: AsyncSession, deps: FakeDeps) -> None:
    chat = await db.get(Chat, world.chat_id)
    assert chat is not None
    chat.ai_enabled = False                       # needs_operator stays True in the world
    await db.commit()
    await api.post(f"/api/chats/{world.chat_id}/ai", json={"enabled": True})
    assert deps.tasks.calls == []


async def test_read_resets_unread(api: ApiClient, world: World, db: AsyncSession) -> None:
    response = await api.post(f"/api/chats/{world.chat_id}/read")
    assert response.status_code == 204
    chat = await db.get(Chat, world.chat_id)
    assert chat is not None and chat.unread_count == 0
    assert (await api.post("/api/chats/999999/read")).status_code == 404


async def test_note_is_an_operator_note_event(api: ApiClient, world: World) -> None:
    response = await api.post(f"/api/chats/{world.chat_id}/notes", json={"text": "не звонить"})
    assert response.status_code == 201
    item = response.json()
    assert item["kind"] == "operator_note"
    assert item["data"] == {"text": "не звонить"}
    assert item["in_context"] is True
    assert (await api.post("/api/chats/999999/notes", json={"text": "x"})).status_code == 404


async def test_operator_done_clears_flag_writes_a_line_and_wakes_the_chat(
        api: ApiClient, world: World, db: AsyncSession, deps: FakeDeps) -> None:
    """«Оператор разобрался» works like switching the AI on: a journal line, and the message
    the client wrote meanwhile gets her phone check, deferred after the commit."""
    response = await api.post(f"/api/chats/{world.chat_id}/operator-done")
    assert response.status_code == 200
    assert response.json()["needs_operator"] is False
    events = (await db.scalars(select(ChatEvent).where(
        ChatEvent.chat_id == world.chat_id, ChatEvent.kind == EventKind.AI_TOGGLED))).all()
    assert [(e.data, e.in_context, e.actor.startswith("operator:")) for e in events] == [
        ({"enabled": True, "scope": "operator"}, False, True)]
    [(name, args, in_transaction)] = deps.tasks.calls
    assert (name, args[0], in_transaction) == ("phone_check", world.persona_id, False)

    # Already done: nothing more is written or woken.
    again = await api.post(f"/api/chats/{world.chat_id}/operator-done")
    assert again.status_code == 200 and again.json()["needs_operator"] is False
    assert len(deps.tasks.calls) == 1
    assert len((await db.scalars(select(ChatEvent).where(
        ChatEvent.chat_id == world.chat_id, ChatEvent.kind == EventKind.AI_TOGGLED))).all()) == 1
    assert (await api.post("/api/chats/999999/operator-done")).status_code == 404


async def test_operator_done_with_nothing_waiting_wakes_nothing(
        api: ApiClient, world: World, db: AsyncSession, deps: FakeDeps) -> None:
    chat = await db.get(Chat, world.chat_id)
    assert chat is not None
    chat.ai_enabled = False                     # the AI gate is still closed in this chat
    await db.commit()
    response = await api.post(f"/api/chats/{world.chat_id}/operator-done")
    assert response.json()["needs_operator"] is False
    assert deps.tasks.calls == []


async def test_persona_status_in_rows_detail_and_its_own_route(api: ApiClient,
                                                               world: World) -> None:
    """«Статус Марины» for the team (docs/attention.md, «Статус для команды»)."""
    [row] = (await api.get("/api/chats")).json()["items"]
    status = row["persona_status"]
    assert (status["code"], status["tone"]) == ("needs_operator", "warn")
    assert status["title"] == "Нужен оператор"
    detail = (await api.get(f"/api/chats/{world.chat_id}")).json()
    assert detail["persona_status"] == status
    assert (await api.get(f"/api/chats/{world.chat_id}/status")).json() == status
    card = (await api.get(f"/api/clients/{world.client_id}")).json()
    assert card["chats"][0]["persona_status"] == status

    # The operator is done: the waiting message is hers again, and she is free.
    done = (await api.post(f"/api/chats/{world.chat_id}/operator-done")).json()
    assert done["persona_status"]["title"] == "Свободна — заглянет в течение минуты"
    fresh_status = (await api.get(f"/api/chats/{world.chat_id}/status")).json()
    # (`until` is her phone check that «Оператор разобрался» asked for.)
    assert {key: fresh_status[key] for key in ("code", "tone", "title", "detail")} == {
        "code": "free", "tone": "wait", "title": "Свободна — заглянет в течение минуты",
        "detail": "без ответа: 1"}
    assert (await api.get("/api/chats/999999/status")).status_code == 404


async def test_reply_run_with_llm_calls(api: ApiClient, world: World, db: AsyncSession) -> None:
    run = ReplyRun(chat_id=world.chat_id, status=RunStatus.SENT, context={"facts": {"a": 1}},
                   plan={"messages": []}, guard_verdicts=[{"verdict": "ok"}])
    db.add(run)
    await db.flush()
    db.add(LlmCall(reply_run_id=run.id, chat_id=world.chat_id, purpose=LlmPurpose.REPLY,
                   model="google/reply", request={"q": 1}, response={"a": 2}, tokens_in=10))
    await db.commit()

    response = await api.get(f"/api/reply-runs/{run.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "sent"
    assert body["context"] == {"facts": {"a": 1}}
    assert body["guard_verdicts"] == [{"verdict": "ok"}]
    [call] = body["llm_calls"]
    assert (call["purpose"], call["model"], call["tokens_in"]) == ("reply", "google/reply", 10)
    assert (await api.get("/api/reply-runs/999999")).status_code == 404


async def test_no_refusals_switch_of_one_chat(api: ApiClient, world: World,
                                              db: AsyncSession) -> None:
    listed = (await api.get("/api/chats")).json()["items"][0]
    assert listed["no_refusals"] is False
    response = await api.post(f"/api/chats/{world.chat_id}/no-refusals", json={"enabled": True})
    assert response.status_code == 200
    assert response.json()["no_refusals"] is True
    chat = await db.get(Chat, world.chat_id)
    assert chat is not None and chat.no_refusals is True
    assert (await api.post("/api/chats/999999/no-refusals",
                           json={"enabled": True})).status_code == 404
