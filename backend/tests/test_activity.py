"""Unified history: identity boundaries, safe metadata, filtering and bounded ordering."""
from datetime import datetime

import pytest
from sqlalchemy import event, select, text

from sim.adapters.db.models import (
    AdminUser,
    Channel,
    Client,
    Dialog,
    LlmCall,
    Message,
    OutboundSendIntent,
    Strategy,
)
from sim.adapters.db.repositories.activity import ActivityFilter, ActivityRepo
from sim.adapters.db.session import transaction
from sim.domain.enums import Author, ChannelKind, Delivery, Direction
from tests.conftest import OPERATOR, OWNER, SUPERADMIN, login, make_world

AT = datetime(2026, 11, 7, 12)


@pytest.fixture
async def history(container, world):
    async with transaction(container.factory) as s:
        (await s.get(Strategy, world.strategy.id)).name = "first"
    other = await make_world(container, persona_name="other")
    async with transaction(container.factory) as s:
        a = Client(persona_id=world.persona.id, display_name="Same name", ref_code="a")
        b = Client(persona_id=other.persona.id, display_name="Same name", ref_code="b")
        archived = Channel(persona_id=world.persona.id, name="archived", kind=ChannelKind.WHATSAPP,
                           is_archived=True)
        s.add_all([a, b, archived])
        await s.flush()
        dialogs = [Dialog(client_id=c.id, channel_id=ch.id, external_thread_id=str(i))
                   for i, (c, ch) in enumerate([(a, world.channel), (a, archived), (b, other.channel)])]
        s.add_all(dialogs)
        await s.flush()
        messages = [
            Message(dialog_id=dialogs[0].id, direction=Direction.IN, author=Author.CLIENT,
                    text="literal %_\\", created_at=AT),
            Message(dialog_id=dialogs[1].id, direction=Direction.OUT, author=Author.AI,
                    text="answer", created_at=AT, delivery=Delivery.FAILED, error="token=SECRET",
                    model="model", provider="provider", tokens_in=0, tokens_out=5, cost_usd=0),
            Message(dialog_id=dialogs[2].id, direction=Direction.OUT, author=Author.OPERATOR,
                    text="separate", created_at=AT),
        ]
        s.add_all(messages)
        await s.flush()
        s.add(OutboundSendIntent(message_id=messages[1].id, fingerprint="f", sent_at=AT))
        calls = [LlmCall(dialog_id=dialogs[1].id, workflow="reply", model="model_%",
                         provider="provider", error="token=SECRET", job_id="SECRET", created_at=AT),
                 LlmCall(workflow="simulation", created_at=AT),
                 LlmCall(workflow="empty-error", error="", created_at=AT)]
        s.add_all(calls)
        await s.flush()
        return {"client": a.id, "other": b.id, "persona": world.persona.id,
                "channel": archived.id, "dialog": dialogs[0].id,
                "messages": [m.id for m in messages], "calls": [c.id for c in calls]}


async def test_message_identity_pagination_metadata_and_no_receipt_mutation(api, container, history):
    await login(api, *OPERATOR)
    page = (await api.get("/api/activity/messages", params={"limit": 1, "offset": 1})).json()
    assert page["total"] == 3 and page["limit"] == 1 and page["offset"] == 1
    row = page["items"][0]
    assert row["id"] == history["messages"][1]
    assert row["tokens_in"] == 0 and row["tokens_out"] == 5 and row["cost_usd"] == 0
    assert row["provider"] == "provider" and row["sent_at"] == "2026-11-07T12:00:00Z"
    assert "SECRET" not in str(page) and row["error"]
    assert row["operator_id"] is None and row["operator_name"] is None
    client = (await api.get(f'/api/clients/{history["client"]}/messages',
                            params={"client_id": history["other"]})).json()
    assert [m["id"] for m in client["items"]] == history["messages"][:2][::-1]
    assert client["items"][1]["sent_at"] is None
    assert client["items"][0]["channel"]["id"] == history["channel"]
    assert (await api.get("/api/clients/999999/messages")).status_code == 404
    assert (await api.get("/api/activity/messages", params={"offset": 99})).json()["items"] == []
    async with container.factory() as s:
        receipt = await s.get(OutboundSendIntent, history["messages"][1])
        assert receipt.fingerprint == "f" and receipt.sent_at == AT


@pytest.mark.parametrize("params,count", [
    ({"q": "%_\\"}, 1), ({"q": "Same name"}, 3), ({"q": "missing"}, 0),
    ({"author": "ai"}, 1), ({"direction": "in"}, 1), ({"delivery": "failed"}, 1),
    ({"channel_kind": "whatsapp"}, 1), ({"from": "2026-11-07", "to": "2026-11-07"}, 3),
    ({"from": "2026-11-07T14:00:00+02:00"}, 3),
    ({"to": "2026-11-07T14:00:00+02:00"}, 0),
])
async def test_message_filters(api, history, params, count):
    await login(api, *OWNER)
    result = await api.get("/api/activity/messages", params=params)
    assert result.status_code == 200, result.text
    assert result.json()["total"] == count


async def test_context_filters(api, history):
    await login(api, *OWNER)
    for key, value, count in [("client_id", history["client"], 2),
                              ("persona_id", history["persona"], 2),
                              ("channel_id", history["channel"], 1)]:
        assert (await api.get("/api/activity/messages", params={key: value})).json()["total"] == count
        assert (await api.get("/api/activity/llm", params={key: value})).json()["total"] == 1


@pytest.mark.parametrize("params", [
    {"limit": 201}, {"limit": 0}, {"offset": -1}, {"client_id": 0}, {"persona_id": -1},
    {"channel_id": 0}, {"channel_kind": "bad"}, {"author": "bad"}, {"direction": "bad"},
    {"delivery": "bad"}, {"q": "x" * 201}, {"from": "bad"}, {"to": "9999-12-31"},
    {"from": "2026-11-08", "to": "2026-11-07"},
])
async def test_validation(api, params):
    await login(api, *OWNER)
    assert (await api.get("/api/activity/messages", params=params)).status_code == 422


async def test_auth_roles(api, history):
    urls = ["/api/activity/messages", "/api/activity/llm", f'/api/clients/{history["client"]}/messages']
    for url in urls:
        assert (await api.get(url)).status_code == 401
    await login(api, *OPERATOR)
    assert (await api.get(urls[1])).status_code == 403
    for role in (OWNER, SUPERADMIN):
        await login(api, *role)
        assert (await api.get(urls[1])).status_code == 200


async def test_llm_no_secrets_unscoped_and_filters(api, history):
    await login(api, *OWNER)
    response = await api.get("/api/activity/llm")
    assert "SECRET" not in response.text and '"error"' not in response.text and "job_id" not in response.text
    rows = response.json()["items"]
    assert [r["id"] for r in rows] == history["calls"][::-1]
    assert rows[0]["client"] is None and rows[0]["channel"] is None
    assert rows[-1]["has_error"] is True
    for params, count in [({"has_error": "true"}, 1), ({"has_error": "false"}, 2),
                          ({"workflow": "reply"}, 1), ({"model": "model_%"}, 1),
                          ({"provider": "provider"}, 1), ({"q": "_%"}, 1),
                          ({"q": "simulation"}, 1), ({"channel_kind": "telegram"}, 0),
                          ({"to": "2026-11-07T12:00:00Z"}, 0)]:
        result = await api.get("/api/activity/llm", params=params)
        assert result.status_code == 200 and result.json()["total"] == count, result.text


async def test_manual_author_snapshot_is_immutable(api, container, history):
    csrf = await login(api, *OWNER)
    result = await api.post(f'/api/dialogs/{history["dialog"]}/messages', json={"text": "manual"},
                            headers={"X-CSRF-Token": csrf})
    assert result.status_code == 201, result.text
    async with transaction(container.factory) as s:
        admin = await s.scalar(select(AdminUser).where(AdminUser.telegram_id == OWNER[0]))
        row = await s.scalar(select(Message).where(Message.text == "manual"))
        assert row.operator_id == admin.id and row.operator_name == admin.display_name
        snapshot = row.operator_name
        admin.display_name = "renamed"
    page = (await api.get(f'/api/clients/{history["client"]}/messages')).json()
    assert next(m for m in page["items"] if m["text"] == "manual")["operator_name"] == snapshot
    async with transaction(container.factory) as s:
        await s.execute(text("PRAGMA foreign_keys=ON"))
        admin = await s.scalar(select(AdminUser).where(AdminUser.telegram_id == OWNER[0]))
        await s.delete(admin)
    async with container.factory() as s:
        row = await s.scalar(select(Message).where(Message.text == "manual"))
        assert row.operator_id is None and row.operator_name == snapshot


async def test_unknown_author_does_not_copy_dialog_operator(container, history):
    async with transaction(container.factory) as s:
        dialog = await s.get(Dialog, history["dialog"])
        row = await container.outbound(s).send_manual(dialog, 999999, "unknown")
        assert row.operator_id is None and row.operator_name is None


async def test_repository_queries_are_bounded_and_no_n_plus_one(container, history):
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.upper())

    event.listen(container.engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with container.factory() as s:
            rows, total = await ActivityRepo(s).messages(ActivityFilter(), 1, 1)
            assert len(rows) == 1 and total == 3
        assert len(statements) == 2
        assert "COUNT(" in statements[0]
        assert "LIMIT" in statements[1] and "OFFSET" in statements[1]
    finally:
        event.remove(container.engine.sync_engine, "before_cursor_execute", capture)


@pytest.mark.parametrize("params", [{"workflow": "x" * 33}, {"model": "x" * 121},
                                    {"provider": "x" * 65}, {"has_error": "invalid"}])
async def test_llm_filter_validation(api, params):
    await login(api, *OWNER)
    assert (await api.get("/api/activity/llm", params=params)).status_code == 422


@pytest.mark.parametrize("activity", ["messages", "llm"])
async def test_combined_filters_use_and_for_count_and_page(api, history, activity):
    await login(api, *OWNER)
    params = {"client_id": history["client"], "persona_id": history["persona"],
              "channel_id": history["channel"], "channel_kind": "whatsapp",
              "from": "2026-11-07", "to": "2026-11-07", "limit": 1, "offset": 0}
    if activity == "messages":
        params.update(q="answer", author="ai", direction="out", delivery="failed")
        expected = history["messages"][1]
    else:
        params.update(q="_%", workflow="reply", model="model_%", provider="provider", has_error="true")
        expected = history["calls"][0]
    url = f"/api/activity/{activity}"
    page = (await api.get(url, params=params)).json()
    assert page["total"] == 1 and [row["id"] for row in page["items"]] == [expected]
    assert page["limit"] == 1 and page["offset"] == 0
    beyond = (await api.get(url, params={**params, "offset": 1})).json()
    assert beyond["total"] == 1 and beyond["items"] == [] and beyond["offset"] == 1
    # Every other predicate still matches; one conflicting predicate must exclude the row.
    conflict = {"direction": "in"} if activity == "messages" else {"has_error": "false"}
    empty = (await api.get(url, params={**params, **conflict})).json()
    assert empty["total"] == 0 and empty["items"] == []
