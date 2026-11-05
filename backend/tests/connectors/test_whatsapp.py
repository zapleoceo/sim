# ruff: noqa: S105, S106
import json

import httpx
import respx

from sim.connectors.whatsapp import WhatsAppConnector
from sim.domain.enums import SessionStatus

EVO = "http://evo.test"
SESSION = {"instance": "sim-3", "webhook_secret": "wsecret"}
JID = "380501112233@s.whatsapp.net"


@respx.mock
async def test_create_payload_includes_webhook_and_safe_flags():
    route = respx.post(f"{EVO}/instance/create").mock(return_value=httpx.Response(
        201, json={"qrcode": {"base64": "data:image/png;base64,AAA"}}))
    res = await WhatsAppConnector().connect(3, {}, {})
    req = route.calls[0].request
    assert req.headers["apikey"] == "global-key"
    body = json.loads(req.content)
    assert body["instanceName"] == "sim-3" and body["integration"] == "WHATSAPP-BAILEYS"
    assert body["qrcode"] is True and body["readMessages"] is False
    assert body["alwaysOnline"] is False and body["syncFullHistory"] is False
    wh = body["webhook"]
    assert wh["url"] == "http://api:8000/hooks/evolution/3"
    assert wh["events"] == ["MESSAGES_UPSERT", "CONNECTION_UPDATE"]
    assert wh["headers"]["apikey"] == res.session["webhook_secret"]
    assert res.status is SessionStatus.PENDING and res.qr_data_uri == "data:image/png;base64,AAA"
    assert res.session["instance"] == "sim-3"


@respx.mock
async def test_create_409_resets_webhook_and_fetches_fresh_qr():
    respx.post(f"{EVO}/instance/create").mock(return_value=httpx.Response(409))
    wh = respx.post(f"{EVO}/webhook/set/sim-3").mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{EVO}/instance/connect/sim-3").mock(
        return_value=httpx.Response(200, json={"base64": "BBB", "count": 1}))
    res = await WhatsAppConnector().connect(3, {}, SESSION)
    assert res.status is SessionStatus.PENDING
    assert res.qr_data_uri == "data:image/png;base64,BBB"
    assert res.session["webhook_secret"] == "wsecret"          # reused, matches Evolution
    sent = json.loads(wh.calls[0].request.content)["webhook"]
    assert sent["headers"] == {"apikey": "wsecret"}


@respx.mock
async def test_create_server_error_is_error_state():
    respx.post(f"{EVO}/instance/create").mock(return_value=httpx.Response(500))
    res = await WhatsAppConnector().connect(3, {}, {})
    assert res.status is SessionStatus.ERROR


def test_verify_webhook_secret():
    c = WhatsAppConnector()
    assert c.verify_webhook({"apikey": "wsecret"}, SESSION)
    assert c.verify_webhook({"ApiKey": "wsecret"}, SESSION)
    assert not c.verify_webhook({"apikey": "global-key"}, SESSION)
    assert not c.verify_webhook({}, SESSION)
    assert not c.verify_webhook({"apikey": "x"}, {})


def _upsert(key=None, message=None):
    data = {"key": {"remoteJid": JID, "fromMe": False, "id": "M1", **(key or {})},
            "pushName": "Olga", "messageTimestamp": 1_700_000_000,
            "message": message if message is not None else {"conversation": "hello"}}
    return {"event": "messages.upsert", "instance": "sim-3", "data": data}


def test_parse_text_variants_and_phone():
    c = WhatsAppConnector()
    [m] = c.parse_webhook(_upsert())
    assert m.text == "hello" and m.external_id == "M1" and m.from_me is False
    assert m.external_thread_id == JID
    assert m.phone == "+380501112233" and m.user_name == "Olga"
    [e] = c.parse_webhook(_upsert(message={"extendedTextMessage": {"text": "reply"}}))
    assert e.text == "reply"
    [i] = c.parse_webhook(_upsert(message={"imageMessage": {"caption": "pic"}}))
    assert (i.text, i.media_kind) == ("pic", "image")
    [lid] = c.parse_webhook(_upsert(key={"remoteJid": "99@lid",
                                         "remoteJidAlt": "380671234567@s.whatsapp.net"}))
    assert lid.phone == "+380671234567" and lid.external_thread_id == "99@lid"


def test_parse_from_me_groups_status_events():
    c = WhatsAppConnector()
    [m] = c.parse_webhook(_upsert(key={"fromMe": True}))
    assert m.from_me is True and m.user_name is None
    assert c.parse_webhook(_upsert(key={"remoteJid": "1-2@g.us"})) == []
    assert c.parse_webhook(_upsert(key={"remoteJid": "status@broadcast"})) == []
    assert c.parse_webhook({"event": "connection.update", "data": {"state": "open"}}) == []
    assert c.parse_webhook({"event": "MESSAGES_UPSERT", "data": [_upsert()["data"]]})


def test_parse_audio_is_voice():
    [m] = WhatsAppConnector().parse_webhook(_upsert(message={"audioMessage": {"ptt": True}}))
    assert m.media_kind == "voice" and m.text == "" and m.media_url == "wa-msg:M1"


@respx.mock
async def test_state_mapping():
    c = WhatsAppConnector()
    cs = respx.get(f"{EVO}/instance/connectionState/sim-3")
    respx.get(f"{EVO}/instance/fetchInstances", params={"instanceName": "sim-3"}).mock(
        return_value=httpx.Response(200, json=[{"name": "sim-3", "ownerJid": JID}]))
    cs.mock(return_value=httpx.Response(200, json={"instance": {"state": "open"}}))
    r = await c.state(3, SESSION)
    assert r.status is SessionStatus.ACTIVE and r.account_label == "+380501112233"
    respx.get(f"{EVO}/instance/connect/sim-3").mock(
        return_value=httpx.Response(200, json={"base64": "CCC"}))
    cs.mock(return_value=httpx.Response(200, json={"instance": {"state": "connecting"}}))
    r = await c.state(3, SESSION)
    assert r.status is SessionStatus.PENDING and r.qr_data_uri.endswith("CCC")
    cs.mock(return_value=httpx.Response(200, json={"instance": {"state": "close"}}))
    assert (await c.state(3, SESSION)).status is SessionStatus.EXPIRED
    cs.mock(return_value=httpx.Response(404))
    assert (await c.state(3, SESSION)).status is SessionStatus.EXPIRED


@respx.mock
async def test_send_text_and_disconnect():
    s = respx.post(f"{EVO}/message/sendText/sim-3").mock(
        return_value=httpx.Response(201, json={"key": {"id": "OUT1"}}))
    res = await WhatsAppConnector().build_port(3, SESSION).send_text(JID, "yo")
    assert res.ok and res.external_message_id == "OUT1"
    assert json.loads(s.calls[0].request.content)["text"] == "yo"
    lo = respx.delete(f"{EVO}/instance/logout/sim-3").mock(return_value=httpx.Response(200))
    de = respx.delete(f"{EVO}/instance/delete/sim-3").mock(return_value=httpx.Response(200))
    await WhatsAppConnector().disconnect(3, SESSION)
    assert lo.called and de.called
