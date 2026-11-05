# ruff: noqa: S105, S106
import json

import httpx
import respx

from sim.adapters.channels.telegram import chunk_text
from sim.connectors.telegram import TelegramConnector
from sim.domain.enums import SessionStatus

API = "https://api.telegram.org/botTOKEN"
SESSION = {"bot_token": "TOKEN", "webhook_secret": "s3cret", "bot_username": "simbot"}
HDR = "X-Telegram-Bot-Api-Secret-Token"


@respx.mock
async def test_connect_sets_webhook_and_returns_session():
    respx.post(f"{API}/getMe").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"username": "simbot"}}))
    hook = respx.post(f"{API}/setWebhook").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True}))
    res = await TelegramConnector().connect(7, {"bot_token": " TOKEN "}, {})
    assert res.status is SessionStatus.ACTIVE and res.account_label == "@simbot"
    body = json.loads(hook.calls[0].request.content)
    assert body["url"] == "https://sim.example.com/hooks/telegram/7"
    assert body["allowed_updates"] == ["message", "edited_message"]
    assert body["drop_pending_updates"] is False
    assert body["secret_token"] == res.session["webhook_secret"]
    assert len(body["secret_token"]) >= 32
    assert res.session["bot_token"] == "TOKEN" and res.session["bot_username"] == "simbot"


@respx.mock
async def test_connect_invalid_token_is_error_without_leaking_token():
    respx.post(f"{API}/getMe").mock(return_value=httpx.Response(
        401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}))
    res = await TelegramConnector().connect(7, {"bot_token": "TOKEN"}, {})
    assert res.status is SessionStatus.ERROR and "TOKEN" not in (res.message or "")


@respx.mock
async def test_network_error_does_not_leak_token():
    respx.post(f"{API}/getMe").mock(side_effect=httpx.ConnectError("boom TOKEN"))
    res = await TelegramConnector().connect(7, {"bot_token": "TOKEN"}, {})
    assert res.status is SessionStatus.ERROR and "TOKEN" not in (res.message or "")


def test_verify_webhook_good_bad_missing_and_case_insensitive():
    c = TelegramConnector()
    assert c.verify_webhook({HDR: "s3cret"}, SESSION)
    assert c.verify_webhook({HDR.lower(): "s3cret"}, SESSION)
    assert not c.verify_webhook({HDR: "nope"}, SESSION)
    assert not c.verify_webhook({}, SESSION)
    assert not c.verify_webhook({HDR: "s3cret"}, {})
    assert not c.verify_webhook({HDR: "é"}, SESSION)   # non-ascii must not raise


def _update(**msg):
    base = {"message_id": 5, "date": 1_700_000_000,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "first_name": "Ann", "last_name": "Lee", "username": "ann"}}
    return {"update_id": 1, "message": {**base, **msg}}


def test_parse_private_text():
    [m] = TelegramConnector().parse_webhook(_update(text="hi"))
    assert (m.external_thread_id, m.external_user_id, m.text) == ("42", "42", "hi")
    assert m.user_name == "Ann Lee" and m.user_handle == "@ann" and m.external_id == "42:5"
    assert m.occurred_at.year == 2023 and m.occurred_at.tzinfo is None


def test_parse_voice_photo_caption_and_edited():
    c = TelegramConnector()
    [v] = c.parse_webhook(_update(voice={"file_id": "V1"}))
    assert (v.media_kind, v.media_url, v.text) == ("voice", "tg-file:V1", "")
    [a] = c.parse_webhook(_update(audio={"file_id": "A1"}))
    assert a.media_kind == "audio"
    [p] = c.parse_webhook(_update(photo=[{"file_id": "s"}, {"file_id": "big"}], caption="look"))
    assert (p.media_kind, p.media_url, p.text) == ("image", "tg-file:big", "look")
    edited = {"edited_message": _update(text="fixed", edit_date=1_700_000_100)["message"]}
    [e] = c.parse_webhook(edited)
    assert e.text == "fixed" and e.external_id != "42:5"


def test_parse_ignores_groups_channels_bots_and_other_updates():
    c = TelegramConnector()
    assert c.parse_webhook(_update(text="x", chat={"id": -1, "type": "group"})) == []
    assert c.parse_webhook(_update(text="x", chat={"id": -1, "type": "supergroup"})) == []
    assert c.parse_webhook({"channel_post": {"text": "x"}}) == []
    assert c.parse_webhook(_update(text="x", **{"from": {"id": 9, "is_bot": True}})) == []
    assert c.parse_webhook({"callback_query": {}}) == []


def test_chunking_respects_limit_and_loses_nothing():
    text = ("word " * 3000).strip()
    parts = chunk_text(text)
    assert len(parts) == 4 and all(len(p) <= 4096 for p in parts)
    assert " ".join(parts).split() == text.split()
    assert len(chunk_text("x" * 9000)) == 3
    assert chunk_text("short") == ["short"]


@respx.mock
async def test_send_text_chunks_long_message():
    route = respx.post(f"{API}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 99}}))
    port = TelegramConnector().build_port(1, SESSION)
    res = await port.send_text("42", "x" * 5000)
    assert res.ok and res.external_message_id == "99" and route.call_count == 2
    first = json.loads(route.calls[0].request.content)
    assert first["chat_id"] == "42" and len(first["text"]) == 4096


@respx.mock
async def test_partial_delivery_is_not_reported_as_failure(caplog):
    """Chunk 2 of 2 fails: a retry would resend chunk 1, so the send counts as done."""
    ok = httpx.Response(200, json={"ok": True, "result": {"message_id": 11}})
    bad = httpx.Response(400, json={"ok": False, "error_code": 400, "description": "boom"})
    route = respx.post(f"{API}/sendMessage").mock(side_effect=[ok, bad])
    res = await TelegramConnector().build_port(1, SESSION).send_text("42", "x" * 5000)
    assert res.ok and res.external_message_id == "11" and route.call_count == 2
    assert "partial delivery" in caplog.text


@respx.mock
async def test_first_chunk_failure_is_still_a_failed_send():
    bad = httpx.Response(400, json={"ok": False, "error_code": 400, "description": "boom"})
    respx.post(f"{API}/sendMessage").mock(return_value=bad)
    res = await TelegramConnector().build_port(1, SESSION).send_text("42", "x" * 5000)
    assert not res.ok


@respx.mock
async def test_send_text_failure_is_result_not_exception():
    respx.post(f"{API}/sendMessage").mock(return_value=httpx.Response(
        403, json={"ok": False, "error_code": 403, "description": "bot was blocked"}))
    res = await TelegramConnector().build_port(1, SESSION).send_text("42", "hi")
    assert not res.ok and "blocked" in res.error


@respx.mock
async def test_state_mapping():
    c = TelegramConnector()
    respx.post(f"{API}/getMe").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}}))
    info = respx.post(f"{API}/getWebhookInfo").mock(return_value=httpx.Response(
        200, json={"ok": True, "result": {"url": "https://sim.example.com/hooks/telegram/7"}}))
    assert (await c.state(7, SESSION)).status is SessionStatus.ACTIVE
    info.mock(return_value=httpx.Response(200, json={"ok": True, "result": {"url": ""}}))
    assert (await c.state(7, SESSION)).status is SessionStatus.ERROR
    respx.post(f"{API}/getMe").mock(return_value=httpx.Response(
        401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}))
    assert (await c.state(7, SESSION)).status is SessionStatus.ERROR


@respx.mock
async def test_disconnect_deletes_webhook_and_download_media():
    d = respx.post(f"{API}/deleteWebhook").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True}))
    await TelegramConnector().disconnect(7, SESSION)
    assert d.called
    respx.post(f"{API}/getFile").mock(return_value=httpx.Response(
        200, json={"ok": True, "result": {"file_path": "voice/a.oga", "file_size": 3}}))
    respx.get("https://api.telegram.org/file/botTOKEN/voice/a.oga").mock(
        return_value=httpx.Response(200, content=b"abc"))
    port = TelegramConnector().build_port(7, SESSION)
    assert await port.download_media("tg-file:V1") == b"abc"


def test_parse_marks_edits():
    c = TelegramConnector()
    [e] = c.parse_webhook({"edited_message": _update(text="fixed", edit_date=1_700_000_100)["message"]})
    [m] = c.parse_webhook(_update(text="hi"))
    assert e.is_edit is True and m.is_edit is False
