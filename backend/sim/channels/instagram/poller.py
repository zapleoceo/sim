"""Receiving Instagram Direct by polling (docs/channels.md, «Приём» and «Темп опроса»).

Each account is polled by a chain: every `channels:poll_instagram` queues the next one of its
account, the pace following the conversation (`PollSchedule`):

  a message in the last 10 min (either side)         every `ig_poll_active_s`   (45 s)
  the last one 10-60 min ago                         every `ig_poll_interval_s` (120 s)
  quiet for an hour, nothing at all, or her night    every `ig_poll_quiet_s`    (300 s)
  (00:00-06:59 in the persona's time zone)

plus up to 25 % random on top; Instagram's «please wait» stretches the next one to 15 min.
`channels:instagram_tick` (every minute) is only the chain's watchdog: an account without a
chain in this process (just connected, a worker restart, a chain that broke) gets a poll.

A poll:

  raw `direct_v2/inbox/` (chats, 20 newest threads, 10 newest items each) and
  `direct_v2/pending_inbox/` (message requests) only while the chats say requests wait
  -> items oldest first (sim/channels/instagram/parse.py)
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
from zoneinfo import ZoneInfo

import httpx

from sim.channels.delivery import RetryAfter
from sim.channels.instagram.account import InstagramAccounts
from sim.channels.instagram.parse import INBOX, PENDING, IgItem, media_text, read_box, thread_items
from sim.domain.enums import ChannelStatus
from sim.ports import ClockPort, InboundAttachment, InboundMessage, Peer, PortError

log = logging.getLogger(__name__)

SEEN_LIMIT = 5000
CATCH_UP = timedelta(hours=24)
MEDIA_MAX_BYTES = 20 * 1024 * 1024
MEDIA_MIME = {"photo": "image/jpeg", "video": "video/mp4", "voice": "audio/mp4"}

ACTIVE_WINDOW = timedelta(minutes=10)   # a message this fresh: the conversation is going on
QUIET_AFTER = timedelta(hours=1)        # nothing for this long: the account is quiet
NIGHT_HOURS = range(0, 7)               # her local 00:00-06:59
JITTER = 0.25                           # up to 25 % random on top of every pause
LATE = timedelta(minutes=2)             # a chain this late is lost: the watchdog restarts it

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


@dataclass(frozen=True)
class Polled:
    """What one poll found, for the pace of the next one. `chain` False: the account is not
    polled at all (gone, switched off, waiting for a new login, own id unknown) — no next poll
    is queued, the watchdog looks again."""

    chain: bool
    count: int = 0                      # new client messages handed to the Inbox
    latest: datetime | None = None      # the newest item of either side
    timezone: str = "UTC"               # the persona's
    backoff_s: float = 0.0              # Instagram asked to wait this long


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
    """The chats, and the message requests only while some wait: the chats' answer counts them
    (`pending_requests_total`), unknown counts as «some». Blocking: run in a thread. A failed
    message-request box is not fatal."""
    chats = read_box(client, INBOX)
    threads = list(chats.threads)
    if chats.pending == 0:
        return threads
    try:
        threads.extend(read_box(client, PENDING).threads)
    except Exception as exc:
        log.warning("instagram %s failed: %s", PENDING, type(exc).__name__)
    return threads


class InstagramPoller:
    def __init__(self, accounts: InstagramAccounts, inbox: Ingest, clock: ClockPort, *,
                 download: Download = download_bounded) -> None:
        self.accounts = accounts
        self.inbox = inbox
        self.clock = clock
        self.download = download
        self.cursors: dict[int, IgCursor] = {}

    async def poll(self, channel_id: int) -> Polled:
        """One poll: new client messages go to the Inbox."""
        try:
            account = await self.accounts.get(channel_id)
        except PortError as exc:
            log.info("instagram channel %s: not polled: %s", channel_id, exc)
            return Polled(chain=False)
        if not account.own_id:
            log.error("instagram channel %s: own id unknown, poll refused", channel_id)
            return Polled(chain=False)
        async with account.lock:
            try:
                threads = await asyncio.to_thread(fetch_threads, account.client)
            except Exception as exc:
                fault = await self.accounts.fault(account, exc, "poll")
                log.info("instagram channel %s: poll skipped: %s", channel_id, fault)
                # A checkpoint waits too: the next poll then finds the channel `challenge`
                # and stops the chain without a request.
                backoff = fault.seconds if isinstance(fault, RetryAfter) else 0.0
                return Polled(chain=True, timezone=account.timezone, backoff_s=backoff)
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
        latest = max((item.at for item in items), default=None)
        return Polled(chain=True, count=count, latest=latest, timezone=account.timezone)

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


@dataclass(frozen=True)
class PollPace:
    """Pauses between two polls of one account, seconds (before the random part)."""

    active_s: float = 45.0
    normal_s: float = 120.0
    quiet_s: float = 300.0


def local_hour(now: datetime, timezone: str) -> int:
    try:
        return now.astimezone(ZoneInfo(timezone or "UTC")).hour
    except (KeyError, ValueError):
        return now.hour


@dataclass
class PollSchedule:
    """When each account is polled next, in the process that runs the polls (the worker)."""

    pace: PollPace
    uniform: Callable[[], float]
    jitter: float = JITTER
    next_at: dict[int, datetime] = field(default_factory=dict)
    latest: dict[int, datetime] = field(default_factory=dict)

    def interval_s(self, channel_id: int, now: datetime, timezone: str) -> float:
        """The pause the conversation of this account asks for now (see the module doc)."""
        latest = self.latest.get(channel_id)
        if local_hour(now, timezone) in NIGHT_HOURS or latest is None:
            return self.pace.quiet_s
        idle = now - latest
        if idle <= ACTIVE_WINDOW:
            return self.pace.active_s
        if idle <= QUIET_AFTER:
            return self.pace.normal_s
        return self.pace.quiet_s

    def after_poll(self, channel_id: int, now: datetime, polled: Polled) -> float:
        """Seconds until the next poll of this account's chain (and remember when it is due)."""
        if polled.latest is not None:
            seen = self.latest.get(channel_id)
            self.latest[channel_id] = max(seen, polled.latest) if seen else polled.latest
        base = max(self.interval_s(channel_id, now, polled.timezone), polled.backoff_s)
        delay = round(base * (1 + self.jitter * self.uniform()), 1)
        self.next_at[channel_id] = now + timedelta(seconds=delay)
        return delay

    def due(self, channel_ids: list[int], now: datetime) -> list[tuple[int, float]]:
        """The watchdog: (channel, delay s) of the accounts whose chain this process does not
        know or that is late; forgets accounts that are gone. A chain waiting in the queue
        after a restart is not doubled: the poll task has one waiting job per account."""
        for gone in set(self.next_at) - set(channel_ids):
            del self.next_at[gone]
        for gone in set(self.latest) - set(channel_ids):
            del self.latest[gone]
        out: list[tuple[int, float]] = []
        for channel_id in channel_ids:
            at = self.next_at.get(channel_id)
            if at is not None and now < at + LATE:
                continue
            self.next_at[channel_id] = now + timedelta(seconds=self.pace.normal_s)
            out.append((channel_id, round(self.pace.normal_s * self.jitter * self.uniform()
                                          / 4, 1)))
        return out
