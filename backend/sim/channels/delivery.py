"""Delivery of one outgoing message, the same for every messenger (docs/telegram.md, «Задача
send_message»; docs/channels.md). Each channel brings only its `MessengerPort`.

  load message + chat + the client's identity in the chat's messenger + media bytes
  -> show «печатает» / «записывает голосовое» for a believable time (1-6 s, by length)
  -> send -> `outbox.mark_sent` (or `mark_failed`)
  -> the chat's live status stops if this message is what the persona was busy with.

A transient error is retried by Procrastinate (`SendRetry`): `RetryAfter` (Telegram FloodWait,
an Instagram soft block) after the seconds the messenger asked for, the rest with a growing
pause. A permanent error, or a transient one on the last attempt, marks the message failed.

Repeats are safe: a message that is no longer pending is skipped. Telegram also drops a repeated
`random_id` (sim/telegram/sending.py). Instagram and WhatsApp have no such id, so their sends are
`exclusive`: the message row is claimed with a transaction-scoped advisory lock for the whole
send and `mark_sent` is written in that same transaction — two jobs of one message never send
at the same time, and the second finds it sent."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from procrastinate.retry import BaseRetryStrategy, RetryDecision
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.db.models import Channel, Chat, ClientIdentity, MediaAsset, Message, PersonaSticker
from sim.db.session import transaction
from sim.domain.channels import spec_of
from sim.domain.enums import ChannelKind, Direction, MessageKind, MessageStatus
from sim.domain.presence import ChatAction, chat_action_for
from sim.ports import ClockPort, MediaStoragePort, MessengerPort, OutboundMessage, Peer, PortError
from sim.services.stickers import ref_of

log = logging.getLogger(__name__)

CHAT_ACTIONS = frozenset(action.value for action in ChatAction)
MAX_RETRIES = 5                     # attempts after the first one
RETRY_BASE_S = 5.0
RETRY_MAX_S = 300.0
ACTION_MIN_S = 1.0
ACTION_MAX_S = 6.0
ACTION_REPEAT_S = 4.5               # Telegram shows a chat action for ~5 s
CHARS_PER_SECOND = 14.0             # a quick typist on a phone
SEND_LOCK_NAMESPACE = 0x51D5        # high 32 bits of the advisory lock key of a send
CLAIM_SQL = text("SELECT pg_try_advisory_xact_lock(CAST(:key AS bigint))")

Sleep = Callable[[float], Awaitable[None]]


class RetryAfter(PortError):
    """The messenger asked to wait `seconds` before the next request (Telegram FloodWait, an
    Instagram soft block). Always retryable."""

    def __init__(self, message: str, seconds: float) -> None:
        super().__init__(message, retryable=True)
        self.seconds = seconds


class PresenceLike(Protocol):
    def sent(self, chat_id: int, kind: str) -> None: ...

    async def show(self, chat_id: int, channel_id: int, peer: Peer, action: str,
                   seconds: float) -> None: ...


# --- retries ---------------------------------------------------------------------------------


def retry_delay(exc: BaseException, attempts: int) -> float | None:
    """Seconds until the next attempt, or None = do not retry."""
    if not isinstance(exc, PortError) or not exc.retryable or attempts >= MAX_RETRIES:
        return None
    if isinstance(exc, RetryAfter):
        return float(exc.seconds) + 1.0
    return min(RETRY_MAX_S, RETRY_BASE_S * 2 ** attempts)


class SendRetry(BaseRetryStrategy):
    def get_retry_decision(self, *, exception: BaseException, job: Any) -> RetryDecision | None:
        delay = retry_delay(exception, int(getattr(job, "attempts", 0) or 0))
        return None if delay is None else RetryDecision(retry_in={"seconds": delay})


def attempts_of(context: Any) -> int:
    return int(getattr(getattr(context, "job", None), "attempts", 0) or 0)


# --- addressing ------------------------------------------------------------------------------


class Outbox(Protocol):
    async def mark_sent(self, session: AsyncSession, message: Message, external_id: str | None,
                        at: Any) -> None: ...
    async def mark_failed(self, session: AsyncSession, message: Message, error: str) -> None: ...


@dataclass(frozen=True)
class Delivery:
    channel_id: int
    outbound: OutboundMessage
    chat_id: int = 0


async def chat_kind(session: AsyncSession, chat: Chat) -> ChannelKind:
    channel = await session.get(Channel, chat.channel_id)
    return ChannelKind(channel.kind) if channel is not None else ChannelKind.TELEGRAM_USER


async def peer_of(session: AsyncSession, chat: Chat) -> Peer:
    """The client's address in the chat's messenger: the identity of that messenger, the
    Telegram access hash as this chat's account sees it, the thread id (Instagram, WhatsApp)."""
    kind = await chat_kind(session, chat)
    identity = (await session.scalars(
        select(ClientIdentity).where(ClientIdentity.client_id == chat.client_id,
                                     ClientIdentity.channel_kind == kind)
        .order_by(ClientIdentity.id).limit(1))).first()
    if identity is None:
        raise PortError(f"the client has no {spec_of(kind).label} identity")
    # The hash is per account: the chat's own one first (the identity keeps the last seen).
    return Peer(external_id=identity.external_id,
                access_hash=chat.peer_access_hash or identity.access_hash,
                thread_id=chat.external_thread_id)


async def chat_address(session: AsyncSession, chat_id: int) -> tuple[int, Peer] | None:
    """(channel id, the client's peer) of a chat; None when the chat is gone. Raises PortError
    when the client has no identity in the chat's messenger."""
    chat = await session.get(Chat, chat_id)
    if chat is None:
        return None
    return chat.channel_id, await peer_of(session, chat)


async def load_delivery(session: AsyncSession, message: Message,
                        storage: MediaStoragePort) -> Delivery:
    address = await chat_address(session, message.chat_id)
    if address is None:
        raise PortError("the chat is gone")
    channel_id, peer = address
    kind = MessageKind(message.kind).value
    if kind == MessageKind.STICKER.value and message.sticker_id is not None:
        # One of her account's stickers (docs/stickers.md): sent by its reference, no file.
        sticker = await session.get(PersonaSticker, message.sticker_id)
        if sticker is None:
            raise PortError("the sticker is gone from her account")
        return Delivery(channel_id, OutboundMessage(peer=peer, kind=kind, text=message.text,
                                                    key=message.id, sticker=ref_of(sticker)),
                        message.chat_id)
    if kind == MessageKind.STICKER.value and message.media_id is None:
        # Her sticker whose row a sync removed (sticker_id is set to NULL): nothing to send.
        raise PortError("the sticker is gone from her account")
    if kind == MessageKind.STICKER.value:
        kind = MessageKind.FILE.value
    if kind == MessageKind.TEXT.value:
        return Delivery(channel_id, OutboundMessage(peer=peer, kind=kind, text=message.text,
                                                    key=message.id), message.chat_id)
    asset = await session.get(MediaAsset, message.media_id) if message.media_id else None
    if asset is None:
        raise PortError(f"{kind} message has no media")
    data = await storage.get(asset.storage_key)
    return Delivery(channel_id, OutboundMessage(
        peer=peer, kind=kind, text=message.text, data=data, mime=asset.mime,
        file_name=asset.file_name, duration_s=asset.duration_s, key=message.id),
        message.chat_id)


# --- sending ---------------------------------------------------------------------------------


def action_seconds(outbound: OutboundMessage) -> float:
    """How long a person would type / record this, bounded to 1-6 s."""
    if outbound.kind == "text":
        base = len(outbound.text) / CHARS_PER_SECOND
    elif outbound.kind in ("voice", "video_note"):
        base = outbound.duration_s or ACTION_MIN_S
    else:
        base = ACTION_MIN_S
    return max(ACTION_MIN_S, min(ACTION_MAX_S, base))


async def show_action(messenger: MessengerPort, delivery: Delivery, sleep: Sleep) -> None:
    action = chat_action_for(delivery.outbound.kind)
    remaining = action_seconds(delivery.outbound)
    while remaining > 0:
        await messenger.set_typing(delivery.channel_id, delivery.outbound.peer, action)
        step = min(remaining, ACTION_REPEAT_S)
        await sleep(step)
        remaining -= step


def _pending_out(message: Message | None) -> bool:
    return (message is not None and message.direction == Direction.OUT
            and message.status == MessageStatus.PENDING)


async def claim_send(session: AsyncSession, message_id: int) -> bool:
    """A transaction-scoped advisory lock on this message's send: False when another job holds
    it right now (it is sending the same message)."""
    key = (SEND_LOCK_NAMESPACE << 32) | (message_id & 0xFFFFFFFF)
    return bool(await session.scalar(CLAIM_SQL, {"key": key}))


async def deliver(
    message_id: int, *, sessions: async_sessionmaker[AsyncSession], storage: MediaStoragePort,
    clock: ClockPort, messenger: MessengerPort, outbox: Outbox, final_attempt: bool,
    sleep: Sleep = asyncio.sleep, presence: PresenceLike | None = None, exclusive: bool = False,
) -> str:
    """Sends one message. Returns `sent`, `failed` or `skipped` (not pending any more);
    raises a retryable PortError when Procrastinate should try again. `exclusive`: the send of
    a messenger without an idempotency key (see the module doc)."""
    send = _deliver_exclusive if exclusive else _deliver_shared
    try:
        delivery = await send(message_id, sessions=sessions, storage=storage, clock=clock,
                              messenger=messenger, outbox=outbox, sleep=sleep)
    except PortError as exc:
        if exc.retryable and not final_attempt:
            log.info("message %s: send will be retried: %s", message_id, exc)
            raise
        await fail(sessions, outbox, message_id, str(exc))
        return "failed"
    if delivery is None:
        return "skipped"
    if presence is not None:
        presence.sent(delivery.chat_id, delivery.outbound.kind)
    return "sent"


async def _deliver_shared(message_id: int, *, sessions: async_sessionmaker[AsyncSession],
                          storage: MediaStoragePort, clock: ClockPort,
                          messenger: MessengerPort, outbox: Outbox,
                          sleep: Sleep) -> Delivery | None:
    """Telegram: load, send outside a transaction, record. A repeat is dropped by its key."""
    async with transaction(sessions) as session:
        message = await session.get(Message, message_id)
        if not _pending_out(message):
            return None
        assert message is not None
        delivery = await load_delivery(session, message, storage)
    await show_action(messenger, delivery, sleep)
    external_id = await messenger.send(delivery.channel_id, delivery.outbound)
    try:
        async with transaction(sessions) as session:
            message = await session.get(Message, message_id)
            if message is not None:
                await outbox.mark_sent(session, message, external_id, clock.now())
    except Exception:
        # Delivered but not recorded: the message stays pending; the reaper queues it again
        # and Telegram drops the repeat by its random_id (sim/services/reaper.py).
        log.exception("message %s: sent (id %s) but not marked sent", message_id, external_id)
        raise
    return delivery


async def _deliver_exclusive(message_id: int, *, sessions: async_sessionmaker[AsyncSession],
                             storage: MediaStoragePort, clock: ClockPort,
                             messenger: MessengerPort, outbox: Outbox,
                             sleep: Sleep) -> Delivery | None:
    """Instagram / WhatsApp: claim, send and record in one transaction."""
    sent: str | None = None
    try:
        async with transaction(sessions) as session:
            if not await claim_send(session, message_id):
                raise PortError("another job is sending this message", retryable=True)
            message = await session.get(Message, message_id)
            if not _pending_out(message):
                return None
            assert message is not None
            delivery = await load_delivery(session, message, storage)
            await show_action(messenger, delivery, sleep)
            sent = await messenger.send(delivery.channel_id, delivery.outbound) or ""
            await outbox.mark_sent(session, message, sent or None, clock.now())
    except PortError:
        raise
    except Exception:
        if sent is not None:
            # The messenger has it, the database does not: the reaper will send it again and
            # the client may get it twice — logged loudly (docs/channels.md, «Повторы»).
            log.exception("message %s: sent (id %s) but not marked sent", message_id, sent)
        raise
    return delivery


async def fail(sessions: async_sessionmaker[AsyncSession], outbox: Outbox, message_id: int,
               error: str) -> None:
    async with transaction(sessions) as session:
        message = await session.get(Message, message_id)
        if message is not None and message.status == MessageStatus.PENDING:
            await outbox.mark_failed(session, message, error[:1000])


async def acknowledge(message_id: int, *, sessions: async_sessionmaker[AsyncSession],
                      messenger: MessengerPort) -> bool:
    """Marks the chat read up to this incoming message. False when there is nothing to mark."""
    async with transaction(sessions) as session:
        message = await session.get(Message, message_id)
        if message is None or message.direction != Direction.IN or not message.external_id:
            return False
        address = await chat_address(session, message.chat_id)
        if address is None:
            return False
        external_id = message.external_id
    channel_id, peer = address
    await messenger.mark_read(channel_id, peer, external_id)
    return True


async def request_presence(chat_id: int, action: str, seconds: float, *,
                           sessions: async_sessionmaker[AsyncSession],
                           presence: PresenceLike) -> bool:
    """Shows (or cancels) the chat's live status. False for an unknown action or chat."""
    if action not in CHAT_ACTIONS:
        log.warning("chat %s: unknown chat action %r", chat_id, action)
        return False
    async with transaction(sessions) as session:
        address = await chat_address(session, chat_id)
    if address is None:
        return False
    channel_id, peer = address
    await presence.show(chat_id, channel_id, peer, action, seconds)
    return True
