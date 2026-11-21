"""Receiving Instagram Direct by polling (docs/channels.md, «Приём»).

Every minute the `channels:instagram_tick` job asks which connected accounts are due (one poll
per `ig_poll_interval_s`, 120 s by default, plus up to 20 % random jitter) and queues their
`channels:poll_instagram`. A poll:

  raw `direct_v2/inbox/` + `direct_v2/pending_inbox/` (chats and message requests, 20 newest
  threads, 10 newest items each) -> items oldest first (sim/channels/instagram/parse.py)
  -> skip our own items (SIM records its sends itself) and ids seen before (`IgCursor`)
  -> skip items older than `CATCH_UP` on the first poll after a start (history is not news)
  -> the media of the item (photo / video / voice from Instagram's CDN, ≤ 20 MB)
  -> `Inbox.ingest`, exactly as a Telegram message (attention, reply, memory unchanged).

The Inbox stores a message once (by item id), so a repeated poll is harmless. Without our own
Instagram id the poll does not run at all (fail closed: never guess who wrote what)."""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

import httpx

from sim.channels.instagram.account import InstagramAccounts
from sim.channels.instagram.parse import INBOXES, IgItem, media_text, paged_threads, thread_items
from sim.domain.enums import ChannelStatus
from sim.ports import ClockPort, InboundAttachment, InboundMessage, Peer, PortError

log = logging.getLogger(__name__)

SEEN_LIMIT = 5000
CATCH_UP = timedelta(hours=24)
MEDIA_MAX_BYTES = 20 * 1024 * 1024
MEDIA_MIME = {"photo": "image/jpeg", "video": "video/mp4", "voice": "audio/mp4"}

Download = Callable[[str], Awaitable[bytes]]


class Ingest(Protocol):
    async def ingest(self, msg: InboundMessage) -> int | None: ...


@dataclass
class IgCursor:
    """Item ids already handed to the Inbox, in this process (ids, not times: a message request
    accepted later brings items OLDER than the last one seen)."""

    seen: OrderedDict[str, None] = field(default_factory=OrderedDict)
    started: bool = False

    def fresh(self, item_id: str) -> bool:
        if item_id in self.seen:
            return False
        self.seen[item_id] = None
        while len(self.seen) > SEEN_LIMIT:
            self.seen.popitem(last=False)
        return True


async def download_bounded(url: str) -> bytes:
    """GET a CDN file over https, at most `MEDIA_MAX_BYTES`."""
    if not url.startswith("https://"):
        raise ValueError("instagram media must be https")
    async with (httpx.AsyncClient(timeout=30, follow_redirects=True) as client,
                client.stream("GET", url) as response):
        response.raise_for_status()
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MEDIA_MAX_BYTES:
                raise ValueError("instagram media is too big")
            chunks.append(chunk)
    return b"".join(chunks)


def fetch_threads(client: Any) -> list[dict[str, Any]]:
    """Both boxes (blocking: run in a thread). A failed message-request box is not fatal."""
    threads: list[dict[str, Any]] = []
    for endpoint in INBOXES:
        try:
            threads.extend(paged_threads(client, endpoint))
        except Exception as exc:
            if endpoint == INBOXES[0]:
                raise
            log.warning("instagram %s failed: %s", endpoint, type(exc).__name__)
    return threads


class InstagramPoller:
    def __init__(self, accounts: InstagramAccounts, inbox: Ingest, clock: ClockPort, *,
                 download: Download = download_bounded) -> None:
        self.accounts = accounts
        self.inbox = inbox
        self.clock = clock
        self.download = download
        self.cursors: dict[int, IgCursor] = {}

    async def poll(self, channel_id: int) -> int:
        """One poll; returns how many new client messages went to the Inbox."""
        try:
            account = await self.accounts.get(channel_id)
        except PortError as exc:
            log.info("instagram channel %s: not polled: %s", channel_id, exc)
            return 0
        if not account.own_id:
            log.error("instagram channel %s: own id unknown, poll refused", channel_id)
            return 0
        async with account.lock:
            try:
                threads = await asyncio.to_thread(fetch_threads, account.client)
            except Exception as exc:
                fault = await self.accounts.fault(account, exc, "poll")
                log.info("instagram channel %s: poll skipped: %s", channel_id, fault)
                return 0
        await self.accounts.remember_session(account)
        items = thread_items(threads, account.own_id)
        now = self.clock.now()
        cursor = self.cursors.setdefault(channel_id, IgCursor())
        first = not cursor.started
        cursor.started = True
        count = 0
        for item in items:
            if not cursor.fresh(item.item_id) or item.from_me:
                continue
            if first and now - item.at > CATCH_UP:
                continue
            if await self.inbox.ingest(await self.inbound(channel_id, item)) is not None:
                count += 1
        await self.accounts.store.set_status(channel_id, ChannelStatus.CONNECTED, None, at=now)
        return count

    async def inbound(self, channel_id: int, item: IgItem) -> InboundMessage:
        attachment = None
        if item.media_kind and item.media_url:
            try:
                data = await self.download(item.media_url)
            except Exception as exc:
                log.info("instagram item %s: media not downloaded (%s)", item.item_id,
                         type(exc).__name__)
                data = b""
            attachment = InboundAttachment(kind=item.media_kind, data=data,
                                           mime=MEDIA_MIME[item.media_kind])
        peer = Peer(external_id=item.user_id, name=item.full_name or (item.username or ""),
                    username=item.username, thread_id=item.thread_id)
        return InboundMessage(channel_id=channel_id, peer=peer, external_id=item.item_id,
                              text=media_text(item.text, item.media_kind), sent_at=item.at,
                              attachment=attachment, reply_to_external_id=item.reply_to)


@dataclass
class PollSchedule:
    """When each account is polled next, in the process that runs the tick (the worker)."""

    interval_s: float
    uniform: Callable[[], float]
    jitter: float = 0.2
    next_at: dict[int, datetime] = field(default_factory=dict)

    def due(self, channel_ids: list[int], now: datetime) -> list[tuple[int, float]]:
        """(channel, delay s) of the accounts to poll now; forgets accounts that are gone."""
        for gone in set(self.next_at) - set(channel_ids):
            del self.next_at[gone]
        out: list[tuple[int, float]] = []
        for channel_id in channel_ids:
            if now < self.next_at.get(channel_id, now):
                continue
            spread = self.interval_s * self.jitter * self.uniform()
            self.next_at[channel_id] = now + timedelta(seconds=self.interval_s + spread)
            out.append((channel_id, round(spread / 4, 1)))
        return out
