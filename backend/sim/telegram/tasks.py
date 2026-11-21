"""Procrastinate tasks of the `telegram` queue, run by the telegram process (it holds the
Telethon sessions).

`send_message(message_id)` delivers one pending outgoing `message` row with the shared delivery
(sim/channels/delivery.py: chat action, send, `outbox.mark_sent` / `mark_failed`, retries).
FloodWait (`RetryAfter`) is retried after the seconds Telegram asked for, the rest with a
growing pause. The outbound message carries the row id as its key, so a retry after a lost
answer is not delivered twice (sim/telegram/sending.py); such a retry is marked sent without a
Telegram id. A job that dies without an outcome leaves the message `pending`: the worker's
stalled-job recovery (sim/tasks/stalled.py) and the reaper (sim/services/reaper.py) queue it
again, and neither delivers twice.

`mark_read(message_id)` marks the chat read up to an incoming message (best effort).

`chat_action(chat_id, action, seconds)` shows a live status («печатает», «записывает кружок»...)
for up to `seconds` through `Presence` (sim/telegram/presence.py); `cancel` stops it.

Wiring: the integrator registers `blueprint` with `app.add_tasks_from(blueprint,
namespace=NAMESPACE)`, so the task names are `telegram:send_message` / `telegram:mark_read` /
`telegram:chat_action` (`SEND_TASK`, `MARK_READ_TASK`, `CHAT_ACTION_TASK`). Deps come from
`sim.services.deps.get_deps()`; the messenger and the presence from `set_runtime` called by the
telegram process."""
from __future__ import annotations

import logging
from types import ModuleType
from typing import Any

from procrastinate import Blueprint, JobContext

from sim.channels.delivery import (
    CHAT_ACTIONS,
    MAX_RETRIES,
    Delivery,
    SendRetry,
    acknowledge,
    action_seconds,
    attempts_of,
    chat_address,
    deliver,
    fail,
    load_delivery,
    peer_of,
    request_presence,
    retry_delay,
    show_action,
)
from sim.ports import MessengerPort, PortError
from sim.telegram.messenger import TelegramMessenger
from sim.telegram.presence import Presence

log = logging.getLogger(__name__)

QUEUE = "telegram"
NAMESPACE = "telegram"
SEND_TASK = f"{NAMESPACE}:send_message"
MARK_READ_TASK = f"{NAMESPACE}:mark_read"
CHAT_ACTION_TASK = f"{NAMESPACE}:chat_action"

# The shared delivery, re-exported for the telegram process and its tests.
__all__ = [
    "CHAT_ACTIONS", "CHAT_ACTION_TASK", "MARK_READ_TASK", "MAX_RETRIES", "NAMESPACE", "QUEUE",
    "SEND_TASK", "Delivery", "SendRetry", "acknowledge", "action_seconds", "blueprint",
    "chat_address", "deliver", "fail", "get_messenger", "get_presence", "load_delivery",
    "peer_of", "request_presence", "retry_delay", "set_runtime", "show_action",
]

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
    await deliver(
        message_id, sessions=deps.sessions, storage=deps.storage, clock=deps.clock,
        messenger=get_messenger(), outbox=_outbox(),
        final_attempt=attempts_of(context) >= MAX_RETRIES, presence=get_presence(),
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
