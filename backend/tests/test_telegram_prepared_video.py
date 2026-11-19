"""Prepared MP4 transport tests: synthetic container boxes, no Telegram connections."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon.tl.types import InputPeerChannel, InputPeerSelf, InputPeerUser

from sim.adapters.channels import telegram_user
from sim.adapters.channels.capabilities import Capability, VideoSender
from sim.adapters.channels.telegram_user import TelegramUserAdapter, TelethonTransport, TGUserState


def box(tag: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + tag + payload


FTYP = box(b"ftyp", b"isom\x00\x00\x00\x00isomiso2")
MP4 = FTYP + box(b"moov", b"synthetic") + box(b"mdat", b"synthetic")


def adapter(entity=None, error=None, receipt_id=42):
    reads = []
    uploads = []

    async def lookup(peer):
        reads.append(peer)
        return entity if entity is not None else InputPeerUser(peer, 42)

    async def upload(peer, file, **kwargs):
        uploads.append((peer, file.read(), file.name, kwargs))
        if error is not None:
            raise error
        return SimpleNamespace(id=receipt_id)

    client = SimpleNamespace(is_connected=lambda: True, get_input_entity=lookup, send_file=upload)
    transport = TelethonTransport(lambda: client)
    transport.ready = AsyncMock(wraps=transport.ready)
    return TelegramUserAdapter(transport, TGUserState(), min_send_interval_s=0), reads, uploads


async def test_prepared_mp4_targets_exact_private_user_with_literal_caption_and_no_circle_flag():
    port, reads, uploads = adapter()
    assert Capability.SEND_VIDEO in port.capabilities and isinstance(port, VideoSender)
    result = await port.send_video("123", MP4, "*keep literal*")
    assert result.ok and result.external_message_id == "123:42" and "123:42" in port.s.sent
    assert reads == [123]
    [(peer, data, name, options)] = uploads
    assert peer.user_id == 123 and data == MP4 and name == "video.mp4"
    assert options == {
        "caption": "*keep literal*", "parse_mode": None, "mime_type": "video/mp4",
        "force_document": False, "supports_streaming": True,
    }


@pytest.mark.parametrize("bad_peer", [
    "", "-123", "0", "777000", "@someone", "１２３", "9" * 4301, str(1 << 63),
])
async def test_invalid_destination_never_opens_transport(bad_peer):
    port, reads, uploads = adapter()
    assert not (await port.send_video(bad_peer, MP4, "")).ok
    port.t.ready.assert_not_awaited()
    assert not reads and not uploads


@pytest.mark.parametrize("entity", [
    InputPeerChannel(123, 42), InputPeerUser(124, 42), InputPeerSelf(),
])
async def test_wrong_resolved_peer_does_not_upload(entity):
    port, reads, uploads = adapter(entity)
    result = await port.send_video("123", MP4, "")
    assert not result.ok and result.error == "private contact required"
    assert reads == [123] and not uploads


@pytest.mark.parametrize("data", [b"", b"not-video", b"<svg>", FTYP, MP4[:-1]])
async def test_invalid_container_never_opens_transport(data):
    port, reads, uploads = adapter()
    assert not (await port.send_video("123", data, "")).ok
    port.t.ready.assert_not_awaited()
    assert not reads and not uploads


async def test_oversized_mp4_never_opens_transport(monkeypatch):
    monkeypatch.setattr(telegram_user, "MEDIA_MAX_BYTES", 4)
    port, reads, uploads = adapter()
    assert not (await port.send_video("123", MP4, "")).ok
    port.t.ready.assert_not_awaited()
    assert not reads and not uploads


async def test_unknown_upload_is_reported_without_replay_or_sensitive_detail():
    port, reads, uploads = adapter(error=TimeoutError("private detail"))
    result = await port.send_video("123", MP4, "")
    assert not result.ok and result.error == "video send uncertain: TimeoutError"
    assert reads == [123] and len(uploads) == 1 and not port.s.sent


@pytest.mark.parametrize(("receipt_id", "error"), [(0, "ValueError"), (None, "TypeError")])
async def test_invalid_receipt_is_uncertain_and_never_marks_delivery(receipt_id, error):
    port, reads, uploads = adapter(receipt_id=receipt_id)
    result = await port.send_video("123", MP4, "")
    assert not result.ok and result.error == f"video send uncertain: {error}"
    assert reads == [123] and len(uploads) == 1 and not port.s.sent
