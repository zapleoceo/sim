"""TelegramMessenger over a fake Telethon client: send kinds, throttling, error mapping."""
from __future__ import annotations

import pytest
from telethon import errors, types

from sim.ports import OutboundMessage, Peer, PortError
from sim.telegram.messenger import (
    CHAT_ACTIONS,
    FloodWait,
    TelegramMessenger,
    chat_action,
    chunks,
    file_name,
    to_port_error,
)
from tests.fakes.telegram import FakeRuntime, FakeTelethonClient, RecordingSleep

PEER = Peer(external_id="1001", access_hash="555")


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


async def make(interval: float = 0.0) -> tuple[TelegramMessenger, FakeTelethonClient,
                                               RecordingSleep, Clock]:
    client = FakeTelethonClient()
    client.connected = True
    sleep, clock = RecordingSleep(), Clock()
    messenger = TelegramMessenger(FakeRuntime(client), min_interval_s=interval, sleep=sleep,
                                  monotonic=clock)
    return messenger, client, sleep, clock


async def test_text_goes_to_the_peer_from_its_access_hash():
    messenger, client, _, _ = await make()
    external_id = await messenger.send(3, OutboundMessage(peer=PEER, kind="text", text="привет"))
    [sent] = client.sent
    assert external_id == str(sent["id"])
    assert sent["text"] == "привет"
    assert isinstance(sent["entity"], types.InputPeerUser)
    assert (sent["entity"].user_id, sent["entity"].access_hash) == (1001, 555)


async def test_without_access_hash_the_entity_cache_is_used():
    messenger, client, _, _ = await make()
    client.entities[1001] = "cached-entity"
    await messenger.send(3, OutboundMessage(peer=Peer(external_id="1001"), kind="text", text="x"))
    assert client.sent[0]["entity"] == "cached-entity"


async def test_unknown_peer_without_hash_is_a_permanent_error():
    messenger, _, _, _ = await make()
    with pytest.raises(PortError) as info:
        await messenger.send(3, OutboundMessage(peer=Peer(external_id="9"), kind="text", text="x"))
    assert not info.value.retryable


async def test_a_long_text_is_split_and_the_first_id_returned():
    messenger, client, _, _ = await make()
    text = ("слово " * 1200).strip()          # 7199 characters: two messages
    external_id = await messenger.send(3, OutboundMessage(peer=PEER, kind="text", text=text))
    assert len(client.sent) == 2
    assert external_id == str(client.sent[0]["id"])
    assert all(len(s["text"]) <= 4096 for s in client.sent)


async def test_voice_is_sent_as_a_voice_note_with_its_duration():
    messenger, client, _, _ = await make()
    await messenger.send(3, OutboundMessage(peer=PEER, kind="voice", data=b"OGG",
                                            mime="audio/ogg", duration_s=4.6))
    [sent] = client.sent
    assert sent["voice_note"] is True and sent["data"] == b"OGG" and sent["name"] == "voice.ogg"
    [attr] = sent["attributes"]
    assert attr.duration == 5 and attr.voice is True


@pytest.mark.parametrize(("kind", "mime", "flags"), [
    ("photo", "image/png", {"caption": "смотри"}),
    ("video", "video/mp4", {"caption": "смотри", "supports_streaming": True}),
    ("video_note", "video/mp4", {"video_note": True}),
    ("file", "application/pdf", {"caption": "смотри", "force_document": True}),
])
async def test_media_kinds(kind, mime, flags):
    messenger, client, _, _ = await make()
    await messenger.send(3, OutboundMessage(peer=PEER, kind=kind, text="смотри", data=b"D",
                                            mime=mime))
    [sent] = client.sent
    for key, value in flags.items():
        assert sent[key] == value
    assert sent["data"] == b"D"


async def test_media_without_data_is_refused():
    messenger, _, _, _ = await make()
    with pytest.raises(PortError):
        await messenger.send(3, OutboundMessage(peer=PEER, kind="photo"))


async def test_sends_of_one_account_are_spaced():
    messenger, _, sleep, clock = await make(interval=1.5)
    await messenger.send(3, OutboundMessage(peer=PEER, kind="text", text="1"))
    clock.t += 0.5
    await messenger.send(3, OutboundMessage(peer=PEER, kind="text", text="2"))
    assert sleep.calls == [pytest.approx(1.0)]


async def test_flood_wait_is_retryable_with_its_seconds():
    messenger, client, _, _ = await make()
    client.errors.append(errors.FloodWaitError(request=None, capture=42))
    with pytest.raises(FloodWait) as info:
        await messenger.send(3, OutboundMessage(peer=PEER, kind="text", text="x"))
    assert info.value.retryable and info.value.seconds == 42


async def test_blocked_by_the_user_is_permanent():
    messenger, client, _, _ = await make()
    client.errors.append(errors.UserIsBlockedError(request=None))
    with pytest.raises(PortError) as info:
        await messenger.send(3, OutboundMessage(peer=PEER, kind="voice", data=b"x"))
    assert not info.value.retryable
    assert "UserIsBlocked" in str(info.value)


async def test_an_offline_account_is_retryable():
    messenger = TelegramMessenger(FakeRuntime(None), min_interval_s=0)
    with pytest.raises(PortError) as info:
        await messenger.send(3, OutboundMessage(peer=PEER, kind="text", text="x"))
    assert info.value.retryable


async def test_typing_is_best_effort():
    messenger, client, _, _ = await make()
    await messenger.set_typing(3, PEER, "record_audio")
    [request] = client.requests
    assert type(request.action).__name__ == "SendMessageRecordAudioAction"
    client.errors.append(ConnectionError("down"))
    await messenger.set_typing(3, PEER, "typing")          # swallowed


async def test_mark_read_acknowledges_up_to_the_message():
    messenger, client, _, _ = await make()
    await messenger.mark_read(3, PEER, "77")
    [(entity, max_id)] = client.read
    assert max_id == 77 and entity.user_id == 1001


def test_error_mapping():
    assert to_port_error(ConnectionError("x")).retryable
    assert to_port_error(TimeoutError()).retryable
    assert not to_port_error(errors.RPCError(request=None, message="X", code=403)).retryable
    assert to_port_error(errors.RPCError(request=None, message="X", code=500)).retryable
    assert not to_port_error(ValueError("no entity")).retryable


def test_chunks_and_names():
    assert chunks("abc") == ["abc"]
    assert chunks("") == [""]
    assert chunks("a" * 10, limit=4) == ["aaaa", "aaaa", "aa"]
    assert file_name(OutboundMessage(peer=PEER, kind="photo", mime="image/png")) == "photo.png"
    assert file_name(OutboundMessage(peer=PEER, kind="file", file_name="a.pdf")) == "a.pdf"
    assert file_name(OutboundMessage(peer=PEER, kind="video_note")) == "video_note.mp4"


def test_chat_actions_map_to_telethon_types():
    assert type(chat_action("upload_photo")).__name__ == "SendMessageUploadPhotoAction"
    assert type(chat_action("unknown")).__name__ == "SendMessageTypingAction"


async def test_video_note_is_a_true_round_message():
    messenger, client, _, _ = await make()
    await messenger.send(3, OutboundMessage(peer=PEER, kind="video_note", data=b"MP4",
                                            mime="video/mp4", duration_s=7.6))
    [sent] = client.sent
    assert sent["video_note"] is True and sent["name"] == "video_note.mp4"
    [attr] = sent["attributes"]
    assert isinstance(attr, types.DocumentAttributeVideo)
    assert attr.round_message is True
    assert (attr.w, attr.h, attr.duration) == (640, 640, 8)


def test_every_chat_action_maps_to_a_telethon_type():
    for action in CHAT_ACTIONS:
        assert type(chat_action(action)).__name__ == CHAT_ACTIONS[action]
    assert type(chat_action("record_round")).__name__ == "SendMessageRecordRoundAction"
    assert type(chat_action("cancel")).__name__ == "SendMessageCancelAction"
