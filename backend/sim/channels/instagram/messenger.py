"""`MessengerPort` for Instagram Direct (docs/channels.md, «Отправка»).

- text: `direct_send(text, thread_ids=[thread])`;
- photo: `direct_send_photo(file, thread_ids=[thread])` (instagrapi 3.2: upload to
  `rupload.facebook.com/messenger_image/` + `photo_attachment`, the retired `configure_photo`
  is gone), the caption goes as a text after it;
- video: `direct_send_video(file.mp4, ...)` (upload to `messenger_video` + `raven_attachment`),
  H.264 mp4 only, the caption as a text after it;
- voice: her OGG/Opus note -> AAC `.m4a` (`MediaToolsPort.to_m4a`) -> `direct_send_voice`
  (upload to `messenger_audio` + `voice_attachment`): a real voice bubble;
- video note, file, sticker: Instagram has none — a permanent error (the plan check never
  asks for them, sim/domain/channels.py). Prod, 2026-11-28: voice and video both delivered.

The address is the chat's Direct thread (`Peer.thread_id`). `mark_read` = the item's
`direct_message_seen` (the client sees «Просмотрено»): one request, and not
`direct_send_seen`, which first reads the thread through instagrapi's extractor — the one that
fails on shared reels and posts. There is no typing status in the private API: `set_typing`
does nothing (the queue does not even defer it, sim/tasks/queue.py)."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from sim.channels.delivery import RetryAfter
from sim.channels.instagram.account import IgAccount, InstagramAccounts
from sim.ports import MediaToolsPort, OutboundMessage, Peer, PortError

PHOTO_SUFFIX = {"image/png": ".png", "image/webp": ".webp"}
# What she sends here -> instagrapi's call (a text is `direct_send`); spec_of(INSTAGRAM).sends.
SENDS = {"text": "direct_send", "photo": "direct_send_photo", "video": "direct_send_video",
         "voice": "direct_send_voice"}
CAPTIONED = frozenset({"photo", "video"})   # a caption follows as its own text


def thread_of(peer: Peer) -> int:
    if not peer.thread_id or not peer.thread_id.isdigit():
        raise PortError("the chat has no Instagram thread")
    return int(peer.thread_id)


def message_id(sent: Any) -> str | None:
    found = getattr(sent, "id", None) or getattr(sent, "item_id", None)
    return str(found) if found else None


class InstagramMessenger:
    def __init__(self, accounts: InstagramAccounts, tools: MediaToolsPort | None = None) -> None:
        self.accounts = accounts
        self.tools = tools

    async def send(self, channel_id: int, message: OutboundMessage) -> str | None:
        if message.kind not in SENDS:
            raise PortError(f"Instagram Direct can not send {message.kind}")
        thread = thread_of(message.peer)
        upload = await self._upload(message)            # converted before the account is held
        account = await self.accounts.get(channel_id)
        async with account.lock:
            wait = account.limiter.wait_s()
            if wait > 0:
                raise RetryAfter("instagram send cap reached", wait)
            try:
                if upload is None:
                    sent = await asyncio.to_thread(account.client.direct_send, message.text,
                                                   thread_ids=[thread])
                else:
                    sent = await self._file(account, thread, message.kind, *upload)
                    if message.kind in CAPTIONED and message.text.strip():
                        await asyncio.to_thread(account.client.direct_send, message.text,
                                                thread_ids=[thread])
            except PortError:
                raise
            except Exception as exc:
                raise await self.accounts.fault(account, exc, "send") from None
            account.limiter.record()
        await self.accounts.remember_session(account)
        return message_id(sent)

    async def _upload(self, message: OutboundMessage) -> tuple[bytes, str] | None:
        """(bytes, file suffix) Instagram takes for this message; None for a text."""
        if message.kind == "text":
            return None
        if not message.data:
            raise PortError(f"{message.kind} message has no data")
        mime = (message.mime or "").lower()
        if message.kind == "photo":
            return message.data, PHOTO_SUFFIX.get(mime, ".jpg")
        if message.kind == "video":
            if mime and mime != "video/mp4":
                raise PortError(f"Instagram Direct takes an mp4 video, not {mime}")
            return message.data, ".mp4"
        if self.tools is None:
            raise PortError("no media tools to convert the voice note")
        return await self.tools.to_m4a(message.data, mime or "audio/ogg"), ".m4a"

    @staticmethod
    async def _file(account: IgAccount, thread: int, kind: str, data: bytes,
                    suffix: str) -> Any:
        send = getattr(account.client, SENDS[kind])
        with tempfile.TemporaryDirectory(prefix="sim-ig-") as folder:
            path = Path(folder) / f"{kind}{suffix}"
            await asyncio.to_thread(path.write_bytes, data)
            return await asyncio.to_thread(send, path, thread_ids=[thread])

    async def set_typing(self, channel_id: int, peer: Peer, action: str) -> None:
        return None

    async def mark_read(self, channel_id: int, peer: Peer, external_id: str) -> None:
        thread = thread_of(peer)
        account = await self.accounts.get(channel_id)
        async with account.lock:
            try:
                if external_id.isdigit():
                    await asyncio.to_thread(account.client.direct_message_seen, thread,
                                            int(external_id))
                else:
                    await asyncio.to_thread(account.client.direct_send_seen, thread)
            except Exception as exc:
                raise await self.accounts.fault(account, exc, "seen") from None
