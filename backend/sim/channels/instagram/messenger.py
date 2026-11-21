"""`MessengerPort` for Instagram Direct (docs/channels.md, «Отправка»).

- text: `direct_send(text, thread_ids=[thread])`;
- photo: `direct_send_photo(file, thread_ids=[thread])`, the caption goes as a text after it;
- voice, video, video note, file: not in the private API — a permanent error (the plan check
  never asks for them).

The address is the chat's Direct thread (`Peer.thread_id`). `mark_read` = `direct_send_seen`
(the client sees «Просмотрено»). There is no typing status in the private API: `set_typing`
does nothing (the queue does not even defer it, sim/tasks/queue.py)."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from sim.channels.delivery import RetryAfter
from sim.channels.instagram.account import IgAccount, InstagramAccounts
from sim.ports import OutboundMessage, Peer, PortError

PHOTO_SUFFIX = {"image/png": ".png", "image/webp": ".webp"}


def thread_of(peer: Peer) -> int:
    if not peer.thread_id or not peer.thread_id.isdigit():
        raise PortError("the chat has no Instagram thread")
    return int(peer.thread_id)


def message_id(sent: Any) -> str | None:
    found = getattr(sent, "id", None) or getattr(sent, "item_id", None)
    return str(found) if found else None


class InstagramMessenger:
    def __init__(self, accounts: InstagramAccounts) -> None:
        self.accounts = accounts

    async def send(self, channel_id: int, message: OutboundMessage) -> str | None:
        if message.kind not in ("text", "photo"):
            raise PortError(f"Instagram Direct can not send {message.kind}")
        thread = thread_of(message.peer)
        account = await self.accounts.get(channel_id)
        async with account.lock:
            wait = account.limiter.wait_s()
            if wait > 0:
                raise RetryAfter("instagram send cap reached", wait)
            try:
                if message.kind == "photo":
                    sent = await self._photo(account, thread, message)
                    if message.text.strip():
                        await asyncio.to_thread(account.client.direct_send, message.text,
                                                thread_ids=[thread])
                else:
                    sent = await asyncio.to_thread(account.client.direct_send, message.text,
                                                   thread_ids=[thread])
            except PortError:
                raise
            except Exception as exc:
                raise await self.accounts.fault(account, exc, "send") from None
            account.limiter.record()
        await self.accounts.remember_session(account)
        return message_id(sent)

    @staticmethod
    async def _photo(account: IgAccount, thread: int, message: OutboundMessage) -> Any:
        if not message.data:
            raise PortError("photo message has no data")
        suffix = PHOTO_SUFFIX.get(message.mime or "", ".jpg")
        with tempfile.TemporaryDirectory(prefix="sim-ig-") as folder:
            path = Path(folder) / f"photo{suffix}"
            await asyncio.to_thread(path.write_bytes, message.data)
            return await asyncio.to_thread(account.client.direct_send_photo, path,
                                           thread_ids=[thread])

    async def set_typing(self, channel_id: int, peer: Peer, action: str) -> None:
        return None

    async def mark_read(self, channel_id: int, peer: Peer, external_id: str) -> None:
        thread = thread_of(peer)
        account = await self.accounts.get(channel_id)
        async with account.lock:
            try:
                await asyncio.to_thread(account.client.direct_send_seen, thread)
            except Exception as exc:
                raise await self.accounts.fault(account, exc, "seen") from None
