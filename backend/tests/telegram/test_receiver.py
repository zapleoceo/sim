"""Incoming Telegram updates -> InboundMessage -> Inbox (no database, fake Telethon objects)."""
from __future__ import annotations

import asyncio

import pytest

from sim.ports import Peer
from sim.telegram.peers import is_person, peer_from_user
from sim.telegram.receiver import MAX_DOWNLOAD_BYTES, Receiver, attachment_kind
from tests.fakes.telegram import (
    FakeDeletedEvent,
    FakeEvent,
    FakeFile,
    FakeInbox,
    FakeTelethonClient,
    FakeTgMessage,
    FakeUser,
)

CHANNEL = 3


async def receive(message: FakeTgMessage, **event_kw: object) -> FakeInbox:
    inbox = FakeInbox()
    await Receiver(inbox).on_new_message(CHANNEL, FakeEvent(message, **event_kw))
    return inbox


async def test_text_message_becomes_an_inbound_message_with_the_peer():
    inbox = await receive(FakeTgMessage(10, "привет", reply_to=7))
    [msg] = inbox.ingested
    assert msg.channel_id == CHANNEL
    assert msg.external_id == "10"
    assert msg.text == "привет"
    assert msg.reply_to_external_id == "7"
    assert msg.attachment is None
    assert msg.sent_at.tzinfo is not None
    assert msg.peer == Peer(external_id="1001", access_hash="555", name="Дима Z",
                            username="dima", phone="+380501234567")


@pytest.mark.parametrize(
    ("media", "file", "kind", "mime"),
    [
        ("photo", FakeFile(size=3, width=800, height=600), "photo", "image/jpeg"),
        ("voice", FakeFile("audio/ogg", 3, duration=14), "voice", "audio/ogg"),
        ("video", FakeFile("video/mp4", 3, "clip.mp4", 5.5, 720, 1280), "video", "video/mp4"),
        ("video_note", FakeFile("video/mp4", 3, duration=9, width=384, height=384),
         "video_note", "video/mp4"),
        ("sticker", FakeFile("image/webp", 3, "sticker.webp", width=512, height=512), "sticker",
         "image/webp"),
        ("audio", FakeFile("audio/mpeg", 3, "song.mp3", 180), "audio", "audio/mpeg"),
        ("document", FakeFile("application/pdf", 3, "договор.pdf"), "file", "application/pdf"),
        ("gif", FakeFile("video/mp4", 3, "anim.mp4"), "video", "video/mp4"),
    ],
)
async def test_every_media_kind_is_downloaded_with_its_metadata(media, file, kind, mime):
    inbox = await receive(FakeTgMessage(11, "подпись", media=media, data=b"abc", file=file))
    [msg] = inbox.ingested
    att = msg.attachment
    assert att is not None
    assert (att.kind, att.mime, att.data) == (kind, mime, b"abc")
    assert att.file_name == (file.name or "")
    assert att.duration_s == (float(file.duration) if file.duration else None)
    assert (att.width, att.height) == (file.width, file.height)
    assert msg.text == "подпись"


async def test_a_file_over_the_limit_is_reported_without_downloading():
    message = FakeTgMessage(12, media="document", data=b"x",
                            file=FakeFile("video/mp4", MAX_DOWNLOAD_BYTES + 1, "big.mp4"))
    inbox = await receive(message)
    att = inbox.ingested[0].attachment
    assert att is not None and att.data == b"" and att.kind == "file"
    assert message.downloads == 0


async def test_a_failed_download_still_records_the_attachment():
    inbox = await receive(FakeTgMessage(13, media="photo", data=b"x", fail_download=True))
    att = inbox.ingested[0].attachment
    assert att is not None and att.kind == "photo" and att.data == b""


async def test_a_link_preview_is_not_an_attachment():
    message = FakeTgMessage(14, "https://example.com", media="photo", data=b"x",
                            web_preview=True)
    inbox = await receive(message)
    assert inbox.ingested[0].attachment is None
    assert message.downloads == 0


@pytest.mark.parametrize("sender", [
    FakeUser(bot=True), FakeUser(deleted=True), FakeUser(is_self=True), FakeUser(id=777000),
])
async def test_bots_service_and_own_account_are_ignored(sender):
    inbox = await receive(FakeTgMessage(15, "x"), sender=sender)
    assert inbox.ingested == []


async def test_group_messages_are_ignored():
    inbox = await receive(FakeTgMessage(16, "x"), is_private=False, chat_id=-100)
    assert inbox.ingested == []


async def test_messages_of_one_person_reach_the_inbox_in_arrival_order():
    inbox = FakeInbox()
    receiver = Receiver(inbox)
    slow = FakeTgMessage(20, media="photo", data=b"p", delay=0.05)
    fast = FakeTgMessage(21, "второе")
    await asyncio.gather(receiver.on_new_message(CHANNEL, FakeEvent(slow)),
                         receiver.on_new_message(CHANNEL, FakeEvent(fast)))
    assert [m.external_id for m in inbox.ingested] == ["20", "21"]


async def test_an_inbox_failure_does_not_break_the_handler():
    class Broken(FakeInbox):
        async def ingest(self, msg):
            raise RuntimeError("db down")

    await Receiver(Broken()).on_new_message(CHANNEL, FakeEvent(FakeTgMessage(22, "x")))


async def test_edits_are_forwarded_with_the_new_text():
    inbox = FakeInbox()
    await Receiver(inbox).on_edited(CHANNEL, FakeEvent(FakeTgMessage(30, "исправила")))
    [(channel_id, peer, external_id, text)] = inbox.edits
    assert (channel_id, peer.external_id, external_id, text) == (CHANNEL, "1001", "30",
                                                                 "исправила")


async def test_edits_outside_private_chats_are_ignored():
    inbox = FakeInbox()
    await Receiver(inbox).on_edited(CHANNEL, FakeEvent(FakeTgMessage(31, "x"), is_private=False))
    assert inbox.edits == []


async def test_private_deletions_are_forwarded_by_message_id():
    inbox = FakeInbox()
    receiver = Receiver(inbox)
    await receiver.on_deleted(CHANNEL, FakeDeletedEvent([40, 41]))
    await receiver.on_deleted(CHANNEL, FakeDeletedEvent([42], chat_id=-1005))   # a channel
    await receiver.on_deleted(CHANNEL, FakeDeletedEvent([]))
    assert inbox.deletions == [(CHANNEL, ["40", "41"])]


async def test_attach_registers_new_edit_and_delete_handlers():
    client = FakeTelethonClient()
    inbox = FakeInbox()
    await Receiver(inbox).attach(CHANNEL, client)
    assert [type(builder).__name__ for _, builder in client.handlers] == [
        "NewMessage", "MessageEdited", "MessageDeleted"]
    handler = client.handlers[0][0]
    await handler(FakeEvent(FakeTgMessage(50, "через handler")))
    assert inbox.ingested[0].channel_id == CHANNEL


def test_attachment_kind_order_and_absence():
    assert attachment_kind(FakeTgMessage(1, "просто текст")) is None
    assert attachment_kind(FakeTgMessage(1, media="voice")) == "voice"   # also a document


def test_peer_helpers():
    assert is_person(FakeUser())
    assert not is_person(None)
    peer = peer_from_user(FakeUser(first_name=None, last_name=None, username=None, phone=None,
                                   access_hash=None))
    assert peer == Peer(external_id="1001", access_hash=None, name="", username=None, phone=None)
