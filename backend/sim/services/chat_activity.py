"""«Марина печатает…» / «записывает голосовое…» while the persona works on a reply, and the
"прочитано" mark on the client's message when she takes it up.

A messenger shows a chat action for a few seconds only, so it is re-sent every REPEAT_S until the
work is done, but never longer than MAX_SHOW_S: a stuck step (a slow model, a pending Broker job)
must not leave «печатает…» hanging. When the work ends the action is cleared at once with CANCEL.
Best effort: no capability, a missing port or a failing call never affects the reply itself."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator

from sim.adapters.channels.capabilities import CANCEL, Capability

log = logging.getLogger(__name__)

REPEAT_S = 4.0
MAX_SHOW_S = 60.0   # the status is never shown longer than this, even if the work hangs


async def send_once(port: object, thread: str, action: str) -> None:
    try:
        await port.send_chat_action(thread, action)  # type: ignore[attr-defined]
    except Exception as exc:  # adapters already swallow; this is the last guard
        log.debug("chat action failed: %s", type(exc).__name__)


async def keep_showing(
    port: object, thread: str, action: str, every: float = REPEAT_S, *, first: bool = True,
    limit: float | None = None,
) -> None:
    """Send `action` (now, unless `first` is False) and then every `every` seconds until
    cancelled or, with `limit`, until that many seconds have passed since the start."""
    deadline = None if limit is None else time.monotonic() + limit
    if not first:
        await asyncio.sleep(every)
    while deadline is None or time.monotonic() < deadline:
        started = time.monotonic()
        await send_once(port, thread, action)
        await asyncio.sleep(max(0.0, every - (time.monotonic() - started)))


async def mark_read(port: object | None, thread: str, external_message_id: str | None) -> None:
    """Shows the client his messages as read up to this one (no capability or id: nothing)."""
    if (port is None or not external_message_id
            or Capability.MARK_READ not in getattr(port, "capabilities", frozenset())):
        return
    try:
        await port.mark_read(thread, external_message_id)  # type: ignore[attr-defined]
    except Exception as exc:  # adapters already swallow; this is the last guard
        log.debug("mark read failed: %s", type(exc).__name__)


@contextlib.asynccontextmanager
async def showing(port: object | None, thread: str, action: str) -> AsyncIterator[None]:
    """`async with showing(port, thread, TYPING): ...` shows the action for the block, at most
    MAX_SHOW_S, and clears it when the block ends."""
    if port is None or Capability.CHAT_ACTION not in getattr(port, "capabilities", frozenset()):
        yield
        return
    # The first action goes out before the work starts, so even a fast step shows it once.
    await send_once(port, thread, action)
    task = asyncio.create_task(
        keep_showing(port, thread, action, REPEAT_S, first=False, limit=MAX_SHOW_S))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await send_once(port, thread, CANCEL)
