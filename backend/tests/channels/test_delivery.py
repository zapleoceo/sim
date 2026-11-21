"""The shared delivery (sim/channels/delivery.py) for Instagram and WhatsApp: the address comes
from the chat's messenger, the send is claimed, sent and marked in one transaction, a repeat is
skipped; and the `channels` jobs (sim/channels/tasks.py)."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from sim.channels import tasks as channel_tasks
from sim.channels.delivery import (
    RetryAfter,
    chat_address,
    claim_send,
    deliver,
    retry_delay,
)
from sim.channels.tasks import DispatchMessenger
from sim.clock import FixedClock
from sim.db.models import ClientIdentity, Message
from sim.db.session import transaction
from sim.domain.enums import Author, ChannelKind, Direction, MessageKind, MessageStatus
from sim.ports import OutboundMessage, Peer, PortError
from sim.telegram.messenger import FloodWait
from tests.channels.conftest import WA_JID, World
from tests.fakes.telegram import (
    FakeMessenger,
    FakeOutbox,
    FakeStorage,
    RecordingSleep,
    TestSessions,
)


async def outgoing(db: AsyncSession, chat_id: int, text: str = "привет)") -> Message:
    message = Message(chat_id=chat_id, direction=Direction.OUT, author=Author.PERSONA,
                      kind=MessageKind.TEXT, text=text, status=MessageStatus.PENDING)
    db.add(message)
    await db.flush()
    await db.commit()
    return message


async def run(message_id: int, sessions: TestSessions, clock: FixedClock,
              messenger: FakeMessenger, outbox: FakeOutbox, *, final: bool = False) -> str:
    return await deliver(message_id, sessions=sessions, storage=FakeStorage(), clock=clock,
                         messenger=messenger, outbox=outbox, final_attempt=final,
                         sleep=RecordingSleep(), exclusive=True)


async def address(sessions: TestSessions, chat_id: int) -> tuple[int, Peer] | None:
    async with transaction(sessions) as session:
        return await chat_address(session, chat_id)


async def test_the_address_is_the_identity_and_thread_of_the_chats_messenger(
        world: World, sessions: TestSessions) -> None:
    ig = await address(sessions, world.ig_chat.id)
    wa = await address(sessions, world.wa_chat.id)
    assert ig == (world.instagram.id, Peer(external_id="1001",
                                           thread_id="340282366841710300949128"))
    assert wa == (world.whatsapp.id, Peer(external_id=WA_JID, thread_id=WA_JID))


async def test_an_exclusive_send_is_marked_in_the_same_transaction_and_never_repeated(
        world: World, db: AsyncSession, sessions: TestSessions, clock: FixedClock) -> None:
    message = await outgoing(db, world.wa_chat.id)
    messenger, outbox = FakeMessenger(external_id="WA1"), FakeOutbox()
    assert await run(message.id, sessions, clock, messenger, outbox) == "sent"
    [(channel_id, outbound)] = messenger.sent
    assert channel_id == world.whatsapp.id and outbound.peer.thread_id == WA_JID
    assert outbox.sent == [(message.id, "WA1", clock.now())]
    assert messenger.actions == [(world.whatsapp.id, "typing")]     # «печатает…» first
    # The repeat (a recovered job, the reaper) finds it sent and does nothing.
    assert await run(message.id, sessions, clock, messenger, outbox) == "skipped"
    assert len(messenger.sent) == 1


async def test_errors_retry_or_fail(world: World, db: AsyncSession, sessions: TestSessions,
                                    clock: FixedClock) -> None:
    # The id, not the row: the rolled-back send expires the session's objects.
    message_id = (await outgoing(db, world.ig_chat.id)).id
    soft = FakeMessenger(error=RetryAfter("instagram soft block", 900))
    with pytest.raises(RetryAfter):
        await run(message_id, sessions, clock, soft, FakeOutbox())
    outbox = FakeOutbox()
    refused = FakeMessenger(error=PortError("Instagram Direct can not send voice"))
    assert await run(message_id, sessions, clock, refused, outbox) == "failed"
    assert outbox.failed == [(message_id, "Instagram Direct can not send voice")]
    assert retry_delay(RetryAfter("wait", 900), 0) == 901.0
    assert retry_delay(FloodWait(30), 0) == 31.0                    # Telegram's is one too
    assert retry_delay(RetryAfter("wait", 900), 5) is None          # the last attempt


async def test_a_client_without_an_identity_there_can_not_be_reached(
        world: World, db: AsyncSession, sessions: TestSessions) -> None:
    await db.execute(delete(ClientIdentity).where(
        ClientIdentity.channel_kind == ChannelKind.INSTAGRAM))
    await db.commit()
    with pytest.raises(PortError, match="no Instagram identity"):
        await address(sessions, world.ig_chat.id)


async def test_the_send_claim_is_held_for_the_transaction(world: World, db: AsyncSession,
                                                         sessions: TestSessions) -> None:
    message = await outgoing(db, world.ig_chat.id)
    async with transaction(sessions) as session:
        assert await claim_send(session, message.id)
        assert await claim_send(session, message.id)               # the same job: re-entrant


async def test_the_dispatcher_picks_the_messenger_of_the_channel(
        world: World, sessions: TestSessions) -> None:
    instagram, whatsapp = FakeMessenger(external_id="IG1"), FakeMessenger(external_id="WA1")
    dispatch = DispatchMessenger(sessions, {ChannelKind.INSTAGRAM: instagram,
                                            ChannelKind.WHATSAPP: whatsapp})
    peer = Peer(external_id="1001", thread_id="1")
    assert await dispatch.send(world.instagram.id, OutboundMessage(peer=peer, kind="text",
                                                                   text="a")) == "IG1"
    assert await dispatch.send(world.whatsapp.id, OutboundMessage(peer=peer, kind="text",
                                                                  text="b")) == "WA1"
    await dispatch.mark_read(world.whatsapp.id, peer, "M1")
    assert len(instagram.sent) == len(whatsapp.sent) == 1 and len(whatsapp.read) == 1
    without_wa = DispatchMessenger(sessions, {ChannelKind.INSTAGRAM: instagram})
    with pytest.raises(PortError, match="Evolution not configured"):
        await without_wa.send(world.whatsapp.id, OutboundMessage(peer=peer, kind="text"))
    with pytest.raises(PortError, match="not found"):
        await dispatch.send(999_999, OutboundMessage(peer=peer, kind="text"))


async def test_the_instagram_tick_queues_the_accounts_that_are_due(clock: FixedClock) -> None:
    deferred: list[tuple[Any, ...]] = []

    class Tasks:
        async def poll_instagram(self, channel_id: int, delay_s: float = 0) -> None:
            deferred.append((channel_id, delay_s))

    class Store:
        async def connected(self, kind: ChannelKind) -> list[int]:
            assert kind == ChannelKind.INSTAGRAM
            return [7, 8]

    class Schedule:
        def due(self, ids: list[int], now: Any) -> list[tuple[int, float]]:
            return [(i, 2.0) for i in ids if i == 7]

    runtime = SimpleNamespace(store=Store(), schedule=Schedule(),
                              deps=SimpleNamespace(tasks=Tasks(), clock=clock))
    channel_tasks.set_runtime(runtime)  # type: ignore[arg-type]
    try:
        await channel_tasks.instagram_tick(0)
    finally:
        channel_tasks.set_runtime(None)
    assert deferred == [(7, 2.0)]
