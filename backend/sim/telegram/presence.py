"""Live chat statuses in Telegram («печатает», «записывает голосовое», «записывает кружок»,
«отправляет фото / видео») for as long as the persona is busy (docs/telegram.md, «Статусы»).

Telegram shows a chat action for about 5 s, so a longer status is re-sent every `repeat_s`
until it expires. Per chat there is one status (action, until):
- `show(...)` starts it or replaces the action and the deadline (sent at once);
- `cancel` (or `seconds <= 0`) stops it and clears the status in Telegram;
- `sent(chat, kind)` after a delivered message stops it when that message is what the persona
  was busy with (`sim.domain.presence.ended_by`).
Lives in the telegram process (it needs the connected clients); `telegram:chat_action` jobs
feed it. Never raises: a failed chat action only costs the client a status line."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sim.domain.presence import MAX_PRESENCE_S, ChatAction, ended_by
from sim.ports import MessengerPort, Peer

log = logging.getLogger(__name__)

REPEAT_S = 4.5                  # Telegram shows a chat action for ~5 s

Sleep = Callable[[float], Awaitable[None]]


@dataclass
class _Status:
    channel_id: int
    peer: Peer
    action: str
    until: float
    task: asyncio.Task[None] | None = None


class Presence:
    def __init__(self, messenger: MessengerPort, *, repeat_s: float = REPEAT_S,
                 max_s: float = MAX_PRESENCE_S, monotonic: Callable[[], float] = time.monotonic,
                 sleep: Sleep = asyncio.sleep) -> None:
        self.messenger = messenger
        self.repeat_s = repeat_s
        self.max_s = max_s
        self.mono = monotonic
        self.sleep = sleep
        self._status: dict[int, _Status] = {}

    def action_of(self, chat_id: int) -> str | None:
        """The status shown in this chat now, if any."""
        status = self._status.get(chat_id)
        return status.action if status is not None else None

    def running(self) -> list[asyncio.Task[None]]:
        return [s.task for s in self._status.values() if s.task is not None and not s.task.done()]

    async def show(self, chat_id: int, channel_id: int, peer: Peer, action: str,
                   seconds: float) -> None:
        if action == ChatAction.CANCEL or seconds <= 0:
            await self.cancel(chat_id, channel_id, peer)
            return
        until = self.mono() + min(float(seconds), self.max_s)
        status = self._status.get(chat_id)
        if status is None or status.task is None or status.task.done():
            status = _Status(channel_id, peer, action, until)
            self._status[chat_id] = status
            status.task = asyncio.create_task(self._refresh(chat_id, status),
                                              name=f"presence-{chat_id}")
        else:
            status.channel_id, status.peer = channel_id, peer
            status.action, status.until = action, until
        await self._send(channel_id, peer, action)

    async def cancel(self, chat_id: int, channel_id: int, peer: Peer) -> None:
        self._stop(chat_id)
        await self._send(channel_id, peer, ChatAction.CANCEL)

    def sent(self, chat_id: int, kind: str) -> None:
        """A message of `kind` was delivered: Telegram drops the action by itself, so only the
        refresh stops (and only if this message ends what the persona was busy with)."""
        status = self._status.get(chat_id)
        if status is not None and ended_by(status.action, kind):
            self._stop(chat_id)

    async def close(self) -> None:
        tasks = self.running()
        for chat_id in list(self._status):
            self._stop(chat_id)
        await asyncio.gather(*tasks, return_exceptions=True)

    def _stop(self, chat_id: int) -> None:
        status = self._status.pop(chat_id, None)
        if status is not None and status.task is not None:
            status.task.cancel()

    async def _refresh(self, chat_id: int, status: _Status) -> None:
        try:
            while (left := status.until - self.mono()) > 0:
                await self.sleep(min(self.repeat_s, left))
                if status.until - self.mono() <= 0:
                    break
                await self._send(status.channel_id, status.peer, status.action)
        finally:
            if self._status.get(chat_id) is status:
                del self._status[chat_id]

    async def _send(self, channel_id: int, peer: Peer, action: str) -> None:
        try:
            await self.messenger.set_typing(channel_id, peer, action)
        except Exception as exc:
            log.debug("chat action %s failed: %s", action, type(exc).__name__)
