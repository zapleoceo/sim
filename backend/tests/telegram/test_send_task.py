"""The `telegram:send_message` delivery: chat action, send, outbox in one transaction, retries."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sim.channels.delivery import load_delivery
from sim.clock import FixedClock
from sim.db.models import MediaAsset, Message, PersonaSticker
from sim.domain.enums import (
    Author,
    Direction,
    MediaKind,
    MediaSource,
    MessageKind,
    MessageStatus,
)
from sim.ports import OutboundMessage, Peer, PortError, StickerRef
from sim.services.stickers import save_account_stickers
from sim.telegram.messenger import FloodWait, TelegramMessenger
from sim.telegram.tasks import (
    MAX_RETRIES,
    SendRetry,
    acknowledge,
    action_seconds,
    deliver,
    request_presence,
    retry_delay,
)
from tests.fakes.telegram import (
    FakeMessenger,
    FakeOutbox,
    FakeRuntime,
    FakeStorage,
    FakeTelethonClient,
    RecordingSleep,
    TestSessions,
)
from tests.telegram.conftest import World, seed_world


async def outgoing(db: AsyncSession, world: World, *, kind: MessageKind = MessageKind.TEXT,
                   text: str = "привет, как ты?", media_id: int | None = None,
                   status: MessageStatus = MessageStatus.PENDING,
                   sticker_id: int | None = None) -> Message:
    message = Message(chat_id=world.chat.id, direction=Direction.OUT, author=Author.PERSONA,
                      kind=kind, text=text, media_id=media_id, status=status,
                      sticker_id=sticker_id)
    db.add(message)
    await db.flush()
    await db.commit()
    return message


async def run(message: Message, sessions: TestSessions, clock: FixedClock, *,
              messenger: FakeMessenger | None = None, storage: FakeStorage | None = None,
              final: bool = False) -> tuple[str, FakeMessenger, FakeOutbox, RecordingSleep]:
    messenger = messenger or FakeMessenger()
    outbox, sleep = FakeOutbox(), RecordingSleep()
    result = await deliver(message.id, sessions=sessions, storage=storage or FakeStorage(),
                           clock=clock, messenger=messenger, outbox=outbox,
                           final_attempt=final, sleep=sleep)
    return result, messenger, outbox, sleep


async def test_text_is_typed_then_sent_and_marked_sent(db, sessions, clock):
    world = await seed_world(db)
    message = await outgoing(db, world)
    result, messenger, outbox, sleep = await run(message, sessions, clock)
    assert result == "sent"
    [(channel_id, outbound)] = messenger.sent
    assert channel_id == world.channel.id
    assert outbound == OutboundMessage(peer=Peer(external_id="1001", access_hash="555"),
                                       kind="text", text="привет, как ты?", key=message.id)
    assert {action for _, action in messenger.actions} == {"typing"}
    assert 1.0 <= sum(sleep.calls) <= 6.0
    assert outbox.sent == [(message.id, "900", clock.now())]
    assert message.status == MessageStatus.SENT


async def test_the_chat_s_own_access_hash_wins(db, sessions, clock):
    """Two accounts see one client with different access hashes: each chat keeps its own."""
    world = await seed_world(db)
    world.chat.peer_access_hash = "777"
    await db.commit()
    message = await outgoing(db, world)
    _, messenger, _, _ = await run(message, sessions, clock)
    [(_, outbound)] = messenger.sent
    assert outbound.peer == Peer(external_id="1001", access_hash="777")


async def test_voice_shows_recording_for_its_length_and_sends_the_file(db, sessions, clock):
    world = await seed_world(db)
    asset = MediaAsset(persona_id=world.persona.id, source=MediaSource.GENERATED,
                       kind=MediaKind.AUDIO, storage_key="voice/1", mime="audio/ogg",
                       sha256="0" * 64, duration_s=3.2)
    db.add(asset)
    await db.flush()
    message = await outgoing(db, world, kind=MessageKind.VOICE, text="", media_id=asset.id)
    storage = FakeStorage({"voice/1": b"OGG"})
    result, messenger, _, sleep = await run(message, sessions, clock, storage=storage)
    assert result == "sent"
    outbound = messenger.sent[0][1]
    assert (outbound.kind, outbound.data, outbound.mime, outbound.duration_s) == (
        "voice", b"OGG", "audio/ogg", 3.2)
    assert {action for _, action in messenger.actions} == {"record_audio"}
    assert sum(sleep.calls) == pytest.approx(3.2)


class UnrecordedOutbox(FakeOutbox):
    """The database fails right after Telegram accepted the message."""

    async def mark_sent(self, session, message, external_id, at):
        raise RuntimeError("database went away")


async def test_a_database_error_after_the_send_leaves_it_pending_for_the_reaper(
        db, sessions, clock):
    world = await seed_world(db)
    message = await outgoing(db, world)
    messenger = FakeMessenger()
    with pytest.raises(RuntimeError):
        await deliver(message.id, sessions=sessions, storage=FakeStorage(), clock=clock,
                      messenger=messenger, outbox=UnrecordedOutbox(), final_attempt=False,
                      sleep=RecordingSleep())
    await db.refresh(message)
    assert len(messenger.sent) == 1 and message.status == MessageStatus.PENDING
    await db.commit()                   # end the read, as the other tests do before `deliver`
    # The reaper queues it again: the repeat carries the same key (Telegram's random_id), so a
    # message Telegram already has is not delivered twice (sim/telegram/sending.py).
    result, again, _, _ = await run(message, sessions, clock)
    assert result == "sent"
    assert again.sent[0][1].key == messenger.sent[0][1].key == message.id


async def test_flood_wait_is_left_to_the_retry(db, sessions, clock):
    world = await seed_world(db)
    message = await outgoing(db, world)
    with pytest.raises(FloodWait):
        await run(message, sessions, clock, messenger=FakeMessenger(error=FloodWait(30)))
    assert message.status == MessageStatus.PENDING


async def test_a_transient_error_on_the_last_attempt_marks_failed(db, sessions, clock):
    world = await seed_world(db)
    message = await outgoing(db, world)
    result, _, outbox, _ = await run(message, sessions, clock, final=True,
                                     messenger=FakeMessenger(error=FloodWait(30)))
    assert result == "failed"
    assert outbox.failed == [(message.id, "telegram flood wait 30s")]
    assert message.status == MessageStatus.FAILED


async def test_a_permanent_error_marks_failed_at_once(db, sessions, clock):
    world = await seed_world(db)
    message = await outgoing(db, world)
    error = PortError("UserIsBlockedError: User is blocked")
    result, _, outbox, _ = await run(message, sessions, clock, messenger=FakeMessenger(error=error))
    assert result == "failed"
    assert outbox.failed == [(message.id, "UserIsBlockedError: User is blocked")]


async def test_a_media_message_without_its_file_fails(db, sessions, clock):
    world = await seed_world(db)
    message = await outgoing(db, world, kind=MessageKind.PHOTO)
    result, messenger, outbox, _ = await run(message, sessions, clock)
    assert result == "failed" and messenger.sent == []
    assert outbox.failed[0][1] == "photo message has no media"


async def test_a_message_that_is_not_pending_is_skipped(db, sessions, clock):
    world = await seed_world(db)
    message = await outgoing(db, world, status=MessageStatus.SENT)
    result, messenger, outbox, _ = await run(message, sessions, clock)
    assert result == "skipped" and messenger.sent == [] and outbox.sent == []


async def test_a_retry_after_a_lost_answer_reaches_the_lead_once(db, sessions, clock):
    """The first attempt reached Telegram but its answer timed out; the retry carries the same
    random_id, Telegram refuses it as a duplicate and the message is marked sent (no id)."""
    world = await seed_world(db)
    message = await outgoing(db, world)
    client = FakeTelethonClient()
    client.connected = True
    client.lost_answers = 1
    messenger = TelegramMessenger(FakeRuntime(client), min_interval_s=0, sleep=RecordingSleep())
    outbox = FakeOutbox()

    async def attempt(final: bool) -> str:
        return await deliver(message.id, sessions=sessions, storage=FakeStorage(), clock=clock,
                             messenger=messenger, outbox=outbox, final_attempt=final,
                             sleep=RecordingSleep())

    with pytest.raises(PortError) as info:
        await attempt(False)
    assert info.value.retryable and message.status == MessageStatus.PENDING
    assert await attempt(True) == "sent"
    assert len(client.sent) == 1
    assert outbox.sent == [(message.id, None, clock.now())]
    assert message.status == MessageStatus.SENT and message.external_id is None


async def test_mark_read_uses_the_incoming_message_id(db, sessions):
    world = await seed_world(db)
    message = Message(chat_id=world.chat.id, direction=Direction.IN, author=Author.CLIENT,
                      kind=MessageKind.TEXT, text="hi", status=MessageStatus.RECEIVED,
                      external_id="321")
    db.add(message)
    await db.flush()
    await db.commit()
    messenger = FakeMessenger()
    assert await acknowledge(message.id, sessions=sessions, messenger=messenger)
    [(channel_id, peer, external_id)] = messenger.read
    assert (channel_id, peer.external_id, external_id) == (world.channel.id, "1001", "321")


def test_retry_delays():
    assert retry_delay(FloodWait(30), 0) == 31.0
    assert retry_delay(PortError("net", retryable=True), 0) == 5.0
    assert retry_delay(PortError("net", retryable=True), 3) == 40.0
    assert retry_delay(PortError("net", retryable=True), 10) is None
    assert retry_delay(PortError("blocked"), 0) is None
    assert retry_delay(RuntimeError("bug"), 0) is None
    assert retry_delay(FloodWait(5), MAX_RETRIES) is None


def test_retry_strategy_decides_from_the_delay():
    strategy = SendRetry()
    assert strategy.get_retry_decision(exception=FloodWait(5),
                                       job=SimpleNamespace(attempts=0)) is not None
    assert strategy.get_retry_decision(exception=PortError("blocked"),
                                       job=SimpleNamespace(attempts=0)) is None


def test_action_seconds_are_bounded():
    peer = Peer(external_id="1")
    assert action_seconds(OutboundMessage(peer=peer, kind="text", text="ок")) == 1.0
    assert action_seconds(OutboundMessage(peer=peer, kind="text", text="x" * 1000)) == 6.0
    assert action_seconds(OutboundMessage(peer=peer, kind="voice", duration_s=2.5)) == 2.5
    assert action_seconds(OutboundMessage(peer=peer, kind="photo")) == 1.0


class RecordingPresence:
    """Presence stand-in: what was asked and which sent messages it was told about."""

    def __init__(self) -> None:
        self.shown: list[tuple[int, int, str, str, float]] = []
        self.sent_kinds: list[tuple[int, str]] = []

    async def show(self, chat_id: int, channel_id: int, peer: Peer, action: str,
                   seconds: float) -> None:
        self.shown.append((chat_id, channel_id, peer.external_id, action, seconds))

    def sent(self, chat_id: int, kind: str) -> None:
        self.sent_kinds.append((chat_id, kind))


async def test_a_delivered_message_is_reported_to_the_presence(db, sessions, clock):
    world = await seed_world(db)
    message = await outgoing(db, world)
    presence = RecordingPresence()
    result = await deliver(message.id, sessions=sessions, storage=FakeStorage(), clock=clock,
                           messenger=FakeMessenger(), outbox=FakeOutbox(), final_attempt=False,
                           sleep=RecordingSleep(), presence=presence)  # type: ignore[arg-type]
    assert result == "sent"
    assert presence.sent_kinds == [(world.chat.id, "text")]


async def test_a_failed_message_leaves_the_presence_alone(db, sessions, clock):
    world = await seed_world(db)
    message = await outgoing(db, world)
    presence = RecordingPresence()
    result = await deliver(message.id, sessions=sessions, storage=FakeStorage(), clock=clock,
                           messenger=FakeMessenger(error=PortError("blocked")),
                           outbox=FakeOutbox(), final_attempt=False, sleep=RecordingSleep(),
                           presence=presence)  # type: ignore[arg-type]
    assert result == "failed" and presence.sent_kinds == []


async def test_chat_action_resolves_the_chat_and_shows_the_status(db, sessions):
    world = await seed_world(db)
    presence = RecordingPresence()
    assert await request_presence(world.chat.id, "record_round", 400.0, sessions=sessions,
                                  presence=presence)  # type: ignore[arg-type]
    assert presence.shown == [(world.chat.id, world.channel.id, "1001", "record_round", 400.0)]


async def test_chat_action_ignores_unknown_actions_and_chats(db, sessions):
    world = await seed_world(db)
    presence = RecordingPresence()
    assert not await request_presence(world.chat.id, "dance", 5.0, sessions=sessions,
                                      presence=presence)  # type: ignore[arg-type]
    assert not await request_presence(999_999, "typing", 5.0, sessions=sessions,
                                      presence=presence)  # type: ignore[arg-type]
    assert presence.shown == []


async def test_video_note_shows_recording_a_circle(db, sessions, clock):
    world = await seed_world(db)
    asset = MediaAsset(persona_id=world.persona.id, source=MediaSource.GENERATED,
                       kind=MediaKind.VIDEO, storage_key="note/1", mime="video/mp4",
                       sha256="0" * 64, duration_s=4.0)
    db.add(asset)
    await db.flush()
    message = await outgoing(db, world, kind=MessageKind.VIDEO_NOTE, text="", media_id=asset.id)
    result, messenger, _, sleep = await run(message, sessions, clock,
                                            storage=FakeStorage({"note/1": b"MP4"}))
    assert result == "sent"
    assert messenger.sent[0][1].kind == "video_note"
    assert {action for _, action in messenger.actions} == {"record_round"}
    assert sum(sleep.calls) == pytest.approx(4.0)


# --- her stickers (docs/stickers.md) ----------------------------------------------------------


async def her_sticker(db: AsyncSession, world: World) -> PersonaSticker:
    sticker = PersonaSticker(persona_id=world.persona.id, channel_id=world.channel.id,
                             doc_id=102, set_id=10, set_access_hash=11, emoji="😂",
                             set_name="ducks", set_title="Утята")
    db.add(sticker)
    await db.flush()
    return sticker


async def test_her_sticker_goes_by_its_reference_without_a_file(db, sessions, clock):
    world = await seed_world(db)
    sticker = await her_sticker(db, world)
    message = await outgoing(db, world, kind=MessageKind.STICKER, text="😂",
                             sticker_id=sticker.id)
    result, messenger, outbox, _ = await run(message, sessions, clock)
    assert result == "sent"
    [(channel_id, outbound)] = messenger.sent
    assert channel_id == world.channel.id
    assert outbound == OutboundMessage(
        peer=Peer(external_id="1001", access_hash="555"), kind="sticker", text="😂",
        key=message.id, sticker=StickerRef(doc_id=102, set_id=10, set_access_hash=11))
    assert outbox.sent == [(message.id, "900", clock.now())]


async def test_her_sticker_reaches_telegram_as_a_sticker(db, sessions, clock):
    world = await seed_world(db)
    sticker = await her_sticker(db, world)
    message = await outgoing(db, world, kind=MessageKind.STICKER, text="😂",
                             sticker_id=sticker.id)
    client = FakeTelethonClient()
    client.connected = True
    client.sticker_sets[10] = SimpleNamespace(packs=[], documents=[
        SimpleNamespace(id=102, access_hash=103, file_reference=b"fresh")])
    messenger = TelegramMessenger(FakeRuntime(client), min_interval_s=0, sleep=RecordingSleep())
    outbox = FakeOutbox()
    result = await deliver(message.id, sessions=sessions, storage=FakeStorage(), clock=clock,
                           messenger=messenger, outbox=outbox, final_attempt=False,
                           sleep=RecordingSleep())
    assert result == "sent"
    [sent] = client.sent
    assert sent["kind"] == "sticker" and sent["document"].id == 102
    assert outbox.sent == [(message.id, str(sent["id"]), clock.now())]


async def test_a_sticker_message_without_her_sticker_still_goes_as_a_file(db, sessions, clock):
    world = await seed_world(db)
    asset = MediaAsset(persona_id=world.persona.id, source=MediaSource.INCOMING,
                       kind=MediaKind.PHOTO, storage_key="sticker/1", mime="image/webp",
                       sha256="0" * 64, file_name="sticker.webp")
    db.add(asset)
    await db.flush()
    message = await outgoing(db, world, kind=MessageKind.STICKER, text="", media_id=asset.id)
    result, messenger, _, _ = await run(message, sessions, clock,
                                        storage=FakeStorage({"sticker/1": b"WEBP"}))
    assert result == "sent"
    outbound = messenger.sent[0][1]
    assert (outbound.kind, outbound.data, outbound.mime, outbound.sticker) == (
        "file", b"WEBP", "image/webp", None)


async def test_a_sticker_row_that_is_gone_can_not_be_delivered(db):
    world = await seed_world(db)
    lost = Message(chat_id=world.chat.id, direction=Direction.OUT, author=Author.PERSONA,
                   kind=MessageKind.STICKER, text="😂", status=MessageStatus.PENDING,
                   sticker_id=999_999)          # not stored: the row it points at is gone
    with pytest.raises(PortError) as info:
        await load_delivery(db, lost, FakeStorage())
    assert not info.value.retryable and "sticker is gone" in str(info.value)


async def test_a_pending_sticker_she_no_longer_has_fails_without_sending(db, sessions, clock):
    world = await seed_world(db)
    sticker = await her_sticker(db, world)
    message = await outgoing(db, world, kind=MessageKind.STICKER, text="😂",
                             sticker_id=sticker.id)
    await save_account_stickers(db, persona_id=world.persona.id, channel_id=world.channel.id,
                                stickers=[])          # the account dropped it before sending
    await db.commit()
    await db.refresh(message)
    result, messenger, outbox, _ = await run(message, sessions, clock)
    assert result == "failed" and messenger.sent == []
    assert [failed_id for failed_id, _ in outbox.failed] == [message.id]
