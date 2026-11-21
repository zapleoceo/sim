"""Procrastinate tasks of the `telegram` queue, run by the telegram process (it holds the
Telethon sessions).

`send_message(message_id)` delivers one pending outgoing `message` row:
  load message + chat + the client's Telegram identity + media bytes
  -> show "typing" / "recording a voice" for a believable time (1-6 s, by length)
  -> send -> `outbox.mark_sent` (or `mark_failed`) in one transaction
  -> the chat's live status stops if this message is what the persona was busy with.
A transient error (FloodWait, network, account reconnecting) is retried by Procrastinate:
FloodWait after the seconds Telegram asked for, the rest with a growing pause. A permanent error,
or a transient one on the last attempt, marks the message failed (a `send_failed` event).

`mark_read(message_id)` marks the chat read up to an incoming message (best effort).

`chat_action(chat_id, action, seconds)` shows a live status («печатает», «записывает кружок»...)
for up to `seconds` through `Presence` (sim/telegram/presence.py); `cancel` stops it.

Wiring: the integrator registers `blueprint` with `app.add_tasks_from(blueprint,
namespace=NAMESPACE)`, so the task names are `telegram:send_message` / `telegram:mark_read` /
`telegram:chat_action` (`SEND_TASK`, `MARK_READ_TASK`, `CHAT_ACTION_TASK`). Deps come from
`sim.services.deps.get_deps()`; the messenger and the presence from `set_runtime` called by the
telegram process."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Protocol

from procrastinate import Blueprint, JobContext
from procrastinate.retry import BaseRetryStrategy, RetryDecision
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.db.models import Chat, ClientIdentity, MediaAsset, Message
from sim.db.session import transaction
from sim.domain.enums import ChannelKind, Direction, MessageKind, MessageStatus
from sim.domain.presence import ChatAction, chat_action_for
from sim.ports import ClockPort, MediaStoragePort, MessengerPort, OutboundMessage, Peer, PortError
from sim.telegram.messenger import FloodWait, TelegramMessenger
from sim.telegram.presence import Presence

log = logging.getLogger(__name__)

QUEUE = "telegram"
NAMESPACE = "telegram"
SEND_TASK = f"{NAMESPACE}:send_message"
MARK_READ_TASK = f"{NAMESPACE}:mark_read"
CHAT_ACTION_TASK = f"{NAMESPACE}:chat_action"
CHAT_ACTIONS = frozenset(action.value for action in ChatAction)

MAX_RETRIES = 5                     # attempts after the first one
RETRY_BASE_S = 5.0
RETRY_MAX_S = 300.0
ACTION_MIN_S = 1.0
ACTION_MAX_S = 6.0
ACTION_REPEAT_S = 4.5               # Telegram shows a chat action for ~5 s
CHARS_PER_SECOND = 14.0             # a quick typist on a phone

Sleep = Callable[[float], Awaitable[None]]

blueprint = Blueprint()


# --- runtime registry (set by the telegram process) ------------------------------------------

_registry: dict[str, Any] = {}


def set_runtime(runtime: Any, messenger: MessengerPort | None = None, *,
                min_interval_s: float = 1.5) -> MessengerPort:
    """Called once by the telegram process after the runtime is created."""
    _registry["runtime"] = runtime
    _registry["messenger"] = messenger or TelegramMessenger(runtime,
                                                            min_interval_s=min_interval_s)
    _registry["presence"] = Presence(_registry["messenger"])
    return _registry["messenger"]


def get_messenger() -> MessengerPort:
    messenger = _registry.get("messenger")
    if messenger is None:
        raise PortError("telegram runtime is not running in this process", retryable=True)
    return messenger


def get_presence() -> Presence:
    presence = _registry.get("presence")
    if presence is None:
        raise PortError("telegram runtime is not running in this process", retryable=True)
    return presence


# --- retries ---------------------------------------------------------------------------------


def retry_delay(exc: BaseException, attempts: int) -> float | None:
    """Seconds until the next attempt, or None = do not retry."""
    if not isinstance(exc, PortError) or not exc.retryable or attempts >= MAX_RETRIES:
        return None
    if isinstance(exc, FloodWait):
        return float(exc.seconds) + 1.0
    return min(RETRY_MAX_S, RETRY_BASE_S * 2 ** attempts)


class SendRetry(BaseRetryStrategy):
    def get_retry_decision(self, *, exception: BaseException, job: Any) -> RetryDecision | None:
        delay = retry_delay(exception, int(getattr(job, "attempts", 0) or 0))
        return None if delay is None else RetryDecision(retry_in={"seconds": delay})


# --- delivery --------------------------------------------------------------------------------


class Outbox(Protocol):
    async def mark_sent(self, session: AsyncSession, message: Message, external_id: str,
                        at: Any) -> None: ...
    async def mark_failed(self, session: AsyncSession, message: Message, error: str) -> None: ...


@dataclass(frozen=True)
class Delivery:
    channel_id: int
    outbound: OutboundMessage
    chat_id: int = 0


def action_seconds(outbound: OutboundMessage) -> float:
    """How long a person would type / record this, bounded to 1-6 s."""
    if outbound.kind == "text":
        base = len(outbound.text) / CHARS_PER_SECOND
    elif outbound.kind in ("voice", "video_note"):
        base = outbound.duration_s or ACTION_MIN_S
    else:
        base = ACTION_MIN_S
    return max(ACTION_MIN_S, min(ACTION_MAX_S, base))


async def peer_of(session: AsyncSession, chat: Chat) -> Peer:
    identity = (await session.scalars(
        select(ClientIdentity).where(ClientIdentity.client_id == chat.client_id,
                                     ClientIdentity.channel_kind == ChannelKind.TELEGRAM_USER)
        .order_by(ClientIdentity.id).limit(1))).first()
    if identity is None:
        raise PortError("the client has no Telegram identity")
    return Peer(external_id=identity.external_id, access_hash=identity.access_hash)


async def chat_address(session: AsyncSession, chat_id: int) -> tuple[int, Peer] | None:
    """(account channel id, the client's peer) of a chat; None when the chat is gone. Raises
    PortError when the client has no Telegram identity."""
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
    if kind == MessageKind.STICKER.value:
        kind = MessageKind.FILE.value
    if kind == MessageKind.TEXT.value:
        return Delivery(channel_id, OutboundMessage(peer=peer, kind=kind, text=message.text),
                        message.chat_id)
    asset = await session.get(MediaAsset, message.media_id) if message.media_id else None
    if asset is None:
        raise PortError(f"{kind} message has no media")
    data = await storage.get(asset.storage_key)
    return Delivery(channel_id, OutboundMessage(
        peer=peer, kind=kind, text=message.text, data=data, mime=asset.mime,
        file_name=asset.file_name, duration_s=asset.duration_s), message.chat_id)


async def show_action(messenger: MessengerPort, delivery: Delivery, sleep: Sleep) -> None:
    action = chat_action_for(delivery.outbound.kind)
    remaining = action_seconds(delivery.outbound)
    while remaining > 0:
        await messenger.set_typing(delivery.channel_id, delivery.outbound.peer, action)
        step = min(remaining, ACTION_REPEAT_S)
        await sleep(step)
        remaining -= step


async def deliver(
    message_id: int, *, sessions: async_sessionmaker[AsyncSession], storage: MediaStoragePort,
    clock: ClockPort, messenger: MessengerPort, outbox: Outbox, final_attempt: bool,
    sleep: Sleep = asyncio.sleep, presence: Presence | None = None,
) -> str:
    """Sends one message. Returns `sent`, `failed` or `skipped` (not pending any more);
    raises a retryable PortError when Procrastinate should try again."""
    try:
        async with transaction(sessions) as session:
            message = await session.get(Message, message_id)
            if (message is None or message.direction != Direction.OUT
                    or message.status != MessageStatus.PENDING):
                return "skipped"
            delivery = await load_delivery(session, message, storage)
        await show_action(messenger, delivery, sleep)
        external_id = await messenger.send(delivery.channel_id, delivery.outbound)
    except PortError as exc:
        if exc.retryable and not final_attempt:
            log.info("message %s: send will be retried: %s", message_id, exc)
            raise
        await fail(sessions, outbox, message_id, str(exc))
        return "failed"
    async with transaction(sessions) as session:
        message = await session.get(Message, message_id)
        if message is not None:
            await outbox.mark_sent(session, message, external_id, clock.now())
    if presence is not None:
        presence.sent(delivery.chat_id, delivery.outbound.kind)
    return "sent"


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
                           presence: Presence) -> bool:
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


# --- Procrastinate tasks ---------------------------------------------------------------------


def _outbox() -> ModuleType:
    from sim.services import outbox

    return outbox


def _deps() -> Any:
    from sim.services.deps import get_deps

    return get_deps()


@blueprint.task(name="send_message", queue=QUEUE, retry=SendRetry(), pass_context=True)
async def send_message(context: JobContext, message_id: int) -> None:
    deps = _deps()
    attempts = int(getattr(context.job, "attempts", 0) or 0)
    await deliver(
        message_id, sessions=deps.sessions, storage=deps.storage, clock=deps.clock,
        messenger=get_messenger(), outbox=_outbox(), final_attempt=attempts >= MAX_RETRIES,
        presence=get_presence(),
    )


@blueprint.task(name="mark_read", queue=QUEUE)
async def mark_read(message_id: int) -> None:
    try:
        await acknowledge(message_id, sessions=_deps().sessions, messenger=get_messenger())
    except PortError as exc:
        log.info("message %s: mark read skipped: %s", message_id, exc)


@blueprint.task(name="chat_action", queue=QUEUE)
async def chat_action(chat_id: int, action: str, seconds: float) -> None:
    try:
        await request_presence(chat_id, action, seconds, sessions=_deps().sessions,
                               presence=get_presence())
    except PortError as exc:
        log.info("chat %s: chat action skipped: %s", chat_id, exc)
