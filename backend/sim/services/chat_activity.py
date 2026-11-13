"""«Марина печатает…» / «записывает голосовое…» while the persona works on a reply.

A messenger shows a chat action for a few seconds only, so it is re-sent every REPEAT_S until the
work is done. Best effort: no capability, a missing port or a failing call never affects the
reply itself."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator

from sim.adapters.channels.capabilities import Capability

log = logging.getLogger(__name__)

REPEAT_S = 4.0


async def keep_showing(port: object, thread: str, action: str, every: float = REPEAT_S) -> None:
    """Send `action` now and then every `every` seconds until cancelled."""
    while True:
        started = time.monotonic()
        try:
            await port.send_chat_action(thread, action)  # type: ignore[attr-defined]
        except Exception as exc:  # adapters already swallow; this is the last guard
            log.debug("chat action failed: %s", type(exc).__name__)
        await asyncio.sleep(max(0.5, every - (time.monotonic() - started)))


@contextlib.asynccontextmanager
async def showing(port: object | None, thread: str, action: str) -> AsyncIterator[None]:
    """`async with showing(port, thread, TYPING): ...` shows the action for the whole block."""
    if port is None or Capability.CHAT_ACTION not in getattr(port, "capabilities", frozenset()):
        yield
        return
    task = asyncio.create_task(keep_showing(port, thread, action, REPEAT_S))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
