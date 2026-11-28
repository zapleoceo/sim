"""WhatsApp over Evolution API with a fake Evolution: parsing its events (text bubbles,
disappearing / view-once envelopes, @lid chats, media in the event), the QR login, sends."""
from __future__ import annotations

import base64
from datetime import UTC, datetime

import pytest

from sim.channels.login import LoginState
from sim.channels.whatsapp.evolution import SECRET_HEADER, EvolutionError, webhook_config
from sim.channels.whatsapp.login import NOT_INSTALLED, WhatsAppLogins
from sim.channels.whatsapp.messenger import WhatsAppMessenger
from sim.channels.whatsapp.parse import (
    connection_state,
    inbound_of,
    owner_phone,
    parse_event,
    phone_of,
    qr_data_uri,
    records_of,
    text_of,
)
from sim.domain.enums import ChannelKind
from sim.ports import OutboundMessage, Peer, PortError
from tests.fakes.channels import FakeEvolution, FakeStore

JID = "79001234567@s.whatsapp.net"


def record(message: dict, *, jid: str = JID, from_me: bool = False, mid: str = "M1",
           **extra: object) -> dict:
    return {"key": {"remoteJid": jid, "fromMe": from_me, "id": mid, **extra},
            "pushName": "Дима", "message": message, "messageTimestamp": 1_763_750_000}


# --- parsing -------------------------------------------------------------------------------


def test_text_of_every_bubble_a_person_reads() -> None:
    assert text_of({"conversation": "привет"}) == "привет"
    assert text_of({"extendedTextMessage": {"text": "смотри https://x.y"}}) == \
        "смотри https://x.y"
    assert text_of({"imageMessage": {"caption": "я"}}) == "я"
    # Disappearing and view-once messages are envelopes around the real one.
    assert text_of({"ephemeralMessage": {"message": {"conversation": "исчезну"}}}) == "исчезну"
    assert text_of({"viewOnceMessageV2": {"message": {"imageMessage": {"caption": "раз"}}}}) \
        == "раз"
    # Bubbles without text of their own get a line.
    assert text_of({"reactionMessage": {"text": "👍"}}) == "👍"
    assert text_of({"reactionMessage": {"text": ""}}) == ""          # taken back
    assert text_of({"locationMessage": {"degreesLatitude": 50.45, "degreesLongitude": 30.52,
                                        "name": "Кафе", "address": "Крещатик, 1"}}) == (
        "📍 геопозиция Кафе · Крещатик, 1 https://maps.google.com/?q=50.45,30.52")
    assert text_of({"contactMessage": {"displayName": "Оля"}}) == "👤 контакт: Оля"
    assert text_of({"contactsArrayMessage": {"contacts": [
        {"displayName": "Оля"}, {"displayName": "Петя"}]}}) == "👤 контакты: Оля, Петя"


def test_a_reaction_is_its_emoji_on_the_message_it_reacts_to() -> None:
    msg = inbound_of(5, record({"reactionMessage": {
        "key": {"remoteJid": JID, "fromMe": True, "id": "OUR-1"}, "text": "❤️"}}, mid="R1"))
    assert msg is not None
    assert (msg.external_id, msg.text, msg.reply_to_external_id, msg.attachment) == (
        "R1", "❤️", "OUR-1", None)
    taken_back = record({"reactionMessage": {"key": {"id": "OUR-1"}, "text": ""}}, mid="R2")
    assert inbound_of(5, taken_back) is None


def test_an_incoming_text_message() -> None:
    msg = inbound_of(5, record({"conversation": "привет"}))
    assert msg is not None
    assert (msg.channel_id, msg.external_id, msg.text) == (5, "M1", "привет")
    assert msg.peer == Peer(external_id=JID, name="Дима", phone="+79001234567", thread_id=JID)
    assert msg.sent_at == datetime.fromtimestamp(1_763_750_000, tz=UTC)
    assert msg.attachment is None


def test_a_reply_keeps_what_it_answers() -> None:
    msg = inbound_of(5, record({"extendedTextMessage": {
        "text": "да", "contextInfo": {"stanzaId": "M0"}}}))
    assert msg is not None and msg.reply_to_external_id == "M0"
    # A voice note or a photo quoting a message answers it too.
    voice = inbound_of(5, record({"audioMessage": {"ptt": True,
                                                   "contextInfo": {"stanzaId": "M7"}}}))
    assert voice is not None and voice.reply_to_external_id == "M7"


def test_an_lid_chat_is_addressed_by_its_phone_when_known() -> None:
    lid = "123456789012345@lid"
    with_phone = inbound_of(5, record({"conversation": "hi"}, jid=lid, remoteJidAlt=JID))
    assert with_phone is not None
    assert (with_phone.peer.external_id, with_phone.peer.thread_id, with_phone.peer.phone) == (
        JID, JID, "+79001234567")
    only_lid = inbound_of(5, record({"conversation": "hi"}, jid=lid))
    assert only_lid is not None
    assert (only_lid.peer.external_id, only_lid.peer.phone) == (lid, None)
    assert phone_of(lid) is None and phone_of("79001234567:12@s.whatsapp.net") == "+79001234567"


def test_own_group_and_status_messages_are_skipped() -> None:
    assert inbound_of(5, record({"conversation": "моё"}, from_me=True)) is None
    assert inbound_of(5, record({"conversation": "всем"}, jid="1203@g.us")) is None
    assert inbound_of(5, record({"conversation": "сторис"}, jid="status@broadcast")) is None
    assert inbound_of(5, record({"protocolMessage": {"type": 0}})) is None
    assert inbound_of(5, "junk") is None


def test_media_comes_in_the_event() -> None:
    ogg = base64.b64encode(b"OGGDATA").decode()
    voice = inbound_of(5, {**record({"audioMessage": {
        "mimetype": "audio/ogg; codecs=opus", "seconds": 7, "ptt": True}}),
        "message": {"audioMessage": {"mimetype": "audio/ogg; codecs=opus", "seconds": 7,
                                     "ptt": True}, "base64": ogg}})
    assert voice is not None and voice.attachment is not None
    assert (voice.attachment.kind, voice.attachment.mime, voice.attachment.data,
            voice.attachment.duration_s) == ("voice", "audio/ogg", b"OGGDATA", 7.0)
    photo = inbound_of(5, record({"viewOnceMessage": {"message": {"imageMessage": {
        "caption": "", "mimetype": "image/jpeg", "width": 800, "height": 600}}}}))
    assert photo is not None and photo.attachment is not None
    # Without base64 the attachment is empty but the Inbox knows a photo came.
    assert (photo.attachment.kind, photo.attachment.data, photo.attachment.width) == (
        "photo", b"", 800)
    song = inbound_of(5, record({"audioMessage": {"mimetype": "audio/mpeg", "ptt": False}}))
    assert song is not None and song.attachment is not None and song.attachment.kind == "audio"


@pytest.mark.parametrize(("message", "kind", "mime"), [
    # The round «video message» (кружок): it used to be dropped as an empty message.
    ({"ptvMessage": {"mimetype": "video/mp4", "seconds": 5, "width": 480, "height": 480}},
     "video_note", "video/mp4"),
    ({"videoMessage": {"mimetype": "video/mp4", "caption": ""}}, "video", "video/mp4"),
    ({"stickerMessage": {"mimetype": "image/webp", "isAnimated": True}}, "sticker",
     "image/webp"),
    ({"stickerMessage": {"mimetype": "image/webp"}}, "sticker", "image/webp"),
    ({"documentMessage": {"mimetype": "application/pdf", "fileName": "cv.pdf"}}, "file",
     "application/pdf"),
])
def test_every_kind_of_media_is_an_attachment(message: dict, kind: str, mime: str) -> None:
    data = base64.b64encode(b"BYTES").decode()
    msg = inbound_of(5, {**record(message), "message": {**message, "base64": data}})
    assert msg is not None and msg.attachment is not None
    assert (msg.attachment.kind, msg.attachment.mime, msg.attachment.data) == (
        kind, mime, b"BYTES")


def test_events_and_answers_of_every_shape() -> None:
    one = record({"conversation": "a"})
    assert records_of(one) == [one]
    assert records_of([one, "x"]) == [one]
    assert records_of({"messages": {"records": [one]}}) == [one]
    assert parse_event({"event": "messages.upsert", "data": one}).records == [one]
    closed = parse_event({"event": "CONNECTION_UPDATE", "data": {"state": "close",
                                                                 "statusReason": 401}})
    assert (closed.kind, closed.state, closed.reason) == ("connection", "close", 401)
    assert parse_event({"event": "qrcode.updated"}).kind == "other"
    assert qr_data_uri({"qrcode": {"base64": "QQ=="}}) == "data:image/png;base64,QQ=="
    assert qr_data_uri({"base64": "data:image/png;base64,Qg=="}) == "data:image/png;base64,Qg=="
    assert connection_state({"instance": {"state": "open"}}) == "open"
    assert owner_phone([{"instance": {"ownerJid": "79001234567@s.whatsapp.net"}}]) == \
        "+79001234567"


def test_the_webhook_carries_our_secret_and_media() -> None:
    config = webhook_config("http://api:8000/api/hooks/evolution/5", "s3cret")
    assert config["headers"] == {SECRET_HEADER: "s3cret"} and config["base64"] is True
    assert config["events"] == ["MESSAGES_UPSERT", "CONNECTION_UPDATE"]


# --- login ---------------------------------------------------------------------------------


async def test_qr_login_creates_the_instance_and_connects() -> None:
    state = {"value": "connecting"}
    api = FakeEvolution({
        ("POST", "/instance/create"): {"qrcode": {"base64": "QQ=="}},
        ("GET", "/instance/connectionState/"): lambda _: {"instance": {"state": state["value"]}},
        ("GET", "/instance/connect/"): {"base64": "Qg=="},
        ("GET", "/instance/fetchInstances"): [{"instance": {"ownerJid": JID}}],
    })
    flow = WhatsAppLogins(api, "http://api:8000/api")
    started = await flow.start(5, None)
    assert (started.state, started.qr) == (LoginState.QR, "data:image/png;base64,QQ==")
    stored = started.credentials
    assert stored is not None and stored["instance"] == "sim-5" and stored["webhook_secret"]
    _, path, body = api.requests[0]
    assert path == "/instance/create"
    assert body is not None and body["instanceName"] == "sim-5" and body["groupsIgnore"]
    assert body["webhook"]["url"] == "http://api:8000/api/hooks/evolution/5"
    assert body["webhook"]["headers"][SECRET_HEADER] == stored["webhook_secret"]
    assert (body["readMessages"], body["alwaysOnline"]) == (False, False)

    waiting = await flow.poll(5, stored)
    assert (waiting.state, waiting.qr) == (LoginState.QR, "data:image/png;base64,Qg==")
    state["value"] = "open"
    done = await flow.poll(5, stored)
    assert (done.state, done.account_label, done.account_id) == (
        LoginState.CONNECTED, "+79001234567", "+79001234567")


async def test_an_existing_instance_gets_our_webhook_again() -> None:
    api = FakeEvolution({
        ("POST", "/instance/create"): EvolutionError("exists", status=403),
        ("GET", "/instance/connectionState/"): {"instance": {"state": "close"}},
        ("GET", "/instance/connect/"): {"base64": "Qg=="},
    })
    started = await WhatsAppLogins(api, "http://api:8000/api").start(
        5, {"instance": "sim-5", "webhook_secret": "old"})
    assert started.state == LoginState.QR and started.credentials == {
        "instance": "sim-5", "webhook_secret": "old"}
    assert ("POST", "/webhook/set/sim-5") in [(m, p) for m, p, _ in api.requests]


async def test_without_evolution_whatsapp_says_it_is_not_installed() -> None:
    flow = WhatsAppLogins(None, "http://x/api")
    assert not flow.ready
    assert (await flow.start(5, None)).message == NOT_INSTALLED
    unreachable = FakeEvolution({("POST", "/instance/create"): EvolutionError("down")})
    result = await WhatsAppLogins(unreachable, "http://x/api").start(5, None)
    assert result.state == LoginState.ERROR and NOT_INSTALLED in result.message


async def test_cancel_unlinks_and_deletes_the_instance() -> None:
    api = FakeEvolution({("DELETE", "/instance/logout/"): EvolutionError("gone", status=404)})
    await WhatsAppLogins(api, "http://x/api").cancel(5, {"instance": "sim-5"})
    assert api.paths("DELETE") == ["/instance/logout/sim-5", "/instance/delete/sim-5"]


# --- sending -------------------------------------------------------------------------------


def whatsapp(api: FakeEvolution) -> WhatsAppMessenger:
    store = FakeStore()
    store.add(5, ChannelKind.WHATSAPP, {"instance": "sim-5", "webhook_secret": "s"})
    return WhatsAppMessenger(api, store)  # type: ignore[arg-type]


async def test_sends_text_voice_and_media() -> None:
    api = FakeEvolution({("POST", "/message/"): {"key": {"id": "WA1"}}})
    messenger = whatsapp(api)
    peer = Peer(external_id=JID, thread_id=JID)
    assert await messenger.send(5, OutboundMessage(peer=peer, kind="text", text="привет")) == "WA1"
    await messenger.send(5, OutboundMessage(peer=peer, kind="voice", data=b"OGG",
                                            mime="audio/ogg"))
    await messenger.send(5, OutboundMessage(peer=peer, kind="photo", text="я", data=b"JPG",
                                            mime="image/jpeg"))
    (_, text_path, text_body), (_, voice_path, voice_body), (_, media_path, media_body) = \
        api.requests
    assert (text_path, text_body) == ("/message/sendText/sim-5", {"number": JID,
                                                                  "text": "привет"})
    assert voice_path == "/message/sendWhatsAppAudio/sim-5"
    assert voice_body == {"number": JID, "audio": base64.b64encode(b"OGG").decode(),
                          "encoding": True}
    assert media_path == "/message/sendMedia/sim-5"
    assert media_body is not None and (media_body["mediatype"], media_body["caption"]) == (
        "image", "я")


async def test_a_round_video_is_whatsapps_video_message() -> None:
    # The owner (2026-11-28): «все функции приложения воцап, как в телеграм».
    api = FakeEvolution()
    peer = Peer(external_id=JID, thread_id=JID)
    await whatsapp(api).send(5, OutboundMessage(peer=peer, kind="video_note", data=b"MP4"))
    (_, path, body), = api.requests
    assert (path, body) == ("/message/sendPtv/sim-5",
                            {"number": JID, "video": base64.b64encode(b"MP4").decode()})
    with pytest.raises(PortError):                      # nothing to send
        await whatsapp(FakeEvolution()).send(5, OutboundMessage(peer=peer, kind="video_note"))


async def test_an_unknown_kind_and_4xx_are_final() -> None:
    peer = Peer(external_id=JID, thread_id=JID)
    with pytest.raises(PortError) as refused:
        await whatsapp(FakeEvolution()).send(5, OutboundMessage(peer=peer, kind="location",
                                                                data=b"?"))
    assert not refused.value.retryable
    assert not EvolutionError("bad", status=400).retryable
    assert EvolutionError("down").retryable and EvolutionError("boom", status=502).retryable


async def test_typing_and_read_receipts() -> None:
    api = FakeEvolution()
    messenger = whatsapp(api)
    peer = Peer(external_id=JID, thread_id=JID)
    await messenger.set_typing(5, peer, "record_audio")
    await messenger.set_typing(5, peer, "record_round")   # no «records a video» in WhatsApp
    await messenger.set_typing(5, peer, "cancel")
    await messenger.mark_read(5, peer, "M1")
    presence = [body["presence"] for m, p, body in api.requests if "sendPresence" in p and body]
    assert presence == ["recording", "composing", "paused"]
    _, path, body = api.requests[-1]
    assert path == "/chat/markMessageAsRead/sim-5"
    assert body == {"readMessages": [{"remoteJid": JID, "fromMe": False, "id": "M1"}]}
