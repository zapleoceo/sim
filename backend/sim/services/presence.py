"""Read receipts and live statuses asked for by the worker (docs/telegram.md, «Прочитано и
статусы»). Both are cosmetic: a failure is logged and never blocks or fails a turn or a
generation, and a slow queue never holds them up for more than `QUEUE_LIMIT_S`."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sim.domain.presence import Busy, ChatAction
from sim.services.queue import TaskQueue

log = logging.getLogger(__name__)

QUEUE_LIMIT_S = 5.0


async def _quietly(what: str, defer: Callable[[], Awaitable[None]]) -> None:
    try:
        await asyncio.wait_for(defer(), QUEUE_LIMIT_S)
    except Exception as exc:
        log.warning("%s not queued: %s", what, str(exc) or type(exc).__name__)


async def mark_read(tasks: TaskQueue, message_id: int | None) -> None:
    """The client sees two ticks up to this incoming message."""
    if message_id is not None:
        await _quietly(f"mark read {message_id}", lambda: tasks.mark_read(message_id))


async def show_busy(tasks: TaskQueue, chat_id: int, busy: Busy) -> None:
    """«печатает» / «записывает голосовое» / ... for up to `busy.seconds`."""
    await _quietly(f"chat {chat_id} {busy.action}",
                   lambda: tasks.chat_action(chat_id, busy.action.value, busy.seconds))


async def stop_busy(tasks: TaskQueue, chat_id: int) -> None:
    await show_busy(tasks, chat_id, Busy(ChatAction.CANCEL, 0.0))
