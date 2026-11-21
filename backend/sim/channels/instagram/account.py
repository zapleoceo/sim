"""Instagram accounts of the process that runs the `channels` queue (the worker): one instagrapi
client per channel, made only when a job needs it and rebuilt when the owner logs in again.

- The client is restored from the stored settings dump with the stored proxy and geo
  (sim/channels/instagram/client.py) — never by logging in again: a fresh login from a server
  is the shortest way to a checkpoint.
- One account does one thing at a time (`lock`): a poll and a send never share the client.
- Send caps per account: `ig_hourly_cap` / `ig_daily_cap` (150 / 800 by default). Over a cap the
  send waits (`RetryAfter`) until a slot frees.
- What an error means (`fault`): a checkpoint or a dead session -> the channel becomes
  `challenge` («нужен повторный вход») and the job waits 15 min; a soft block («please wait»,
  feedback_required, 429) -> waits 15 min; the network -> a normal retry; no such thread / user
  -> permanent.
Counters live in memory: a restart forgets them (documented in docs/channels.md)."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sim.channels.delivery import RetryAfter
from sim.channels.instagram.client import ClientFactory, IgError, build_ig_client, classify
from sim.channels.store import ChannelStore, StoredAccount
from sim.domain.enums import ChannelKind, ChannelStatus
from sim.ports import ClockPort, PortError

log = logging.getLogger(__name__)

SOFT_BLOCK_S = 900.0                    # «please wait» / challenge: try again in 15 minutes
HOUR_S, DAY_S = 3600.0, 86400.0
RELOGIN = "Instagram просит войти заново (checkpoint): переподключите аккаунт"


class SendLimiter:
    """At most `hourly` sends in any hour and `daily` in any day."""

    def __init__(self, hourly: int, daily: int, monotonic: Callable[[], float]) -> None:
        self.hourly, self.daily = hourly, daily
        self.monotonic = monotonic
        self.sent: deque[float] = deque()

    def wait_s(self) -> float:
        """0 when a send may go now, else seconds until a slot frees."""
        now = self.monotonic()
        while self.sent and now - self.sent[0] >= DAY_S:
            self.sent.popleft()
        last_hour = [at for at in self.sent if now - at < HOUR_S]
        if len(last_hour) >= self.hourly:
            return HOUR_S - (now - last_hour[0])
        if len(self.sent) >= self.daily:
            return DAY_S - (now - self.sent[0])
        return 0.0

    def record(self) -> None:
        self.sent.append(self.monotonic())


@dataclass
class IgAccount:
    channel_id: int
    client: Any
    own_id: str
    token: str = field(repr=False)
    credentials: dict[str, Any] = field(repr=False)
    limiter: SendLimiter
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class InstagramAccounts:
    def __init__(self, store: ChannelStore, clock: ClockPort, *, hourly_cap: int,
                 daily_cap: int, default_proxy: str = "",
                 factory: ClientFactory = build_ig_client,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.store = store
        self.clock = clock
        self.hourly_cap, self.daily_cap = hourly_cap, daily_cap
        self.default_proxy = default_proxy
        self.factory = factory
        self.monotonic = monotonic
        self._accounts: dict[int, IgAccount] = {}

    async def get(self, channel_id: int) -> IgAccount:
        stored = await self.store.account(channel_id, ChannelKind.INSTAGRAM)
        if stored.status == ChannelStatus.CHALLENGE:
            raise RetryAfter(RELOGIN, SOFT_BLOCK_S)
        account = self._accounts.get(channel_id)
        if account is None or account.token != stored.token:
            account = await self._build(stored, account)
            self._accounts[channel_id] = account
        return account

    async def _build(self, stored: StoredAccount, old: IgAccount | None) -> IgAccount:
        creds = stored.credentials
        settings = creds.get("settings")
        if not isinstance(settings, dict) or not settings:
            raise PortError(f"instagram channel {stored.channel_id} has no session")
        proxy = str(creds.get("proxy") or "") or self.default_proxy
        client = await asyncio.to_thread(
            self.factory, settings, proxy=proxy, lang=str(creds.get("lang") or ""),
            tz_offset_s=creds.get("tz_offset_s"))
        own = str((settings.get("authorization_data") or {}).get("ds_user_id") or "")
        limiter = old.limiter if old is not None else SendLimiter(
            self.hourly_cap, self.daily_cap, self.monotonic)
        return IgAccount(stored.channel_id, client, own, stored.token, dict(creds), limiter)

    def forget(self, channel_id: int) -> None:
        self._accounts.pop(channel_id, None)

    async def remember_session(self, account: IgAccount) -> None:
        """Store the client's refreshed cookies when they changed (best effort)."""
        try:
            settings = account.client.get_settings()
        except Exception as exc:
            log.debug("instagram channel %s: no settings (%s)", account.channel_id,
                      type(exc).__name__)
            return
        if settings == account.credentials.get("settings"):
            return
        account.credentials["settings"] = settings
        await self.store.save(account.channel_id, account.credentials)
        # The stored blob changed: keep this client instead of rebuilding it on the next job.
        stored = await self.store.account(account.channel_id, ChannelKind.INSTAGRAM)
        account.token = stored.token

    async def fault(self, account: IgAccount, exc: BaseException, what: str) -> PortError:
        """The PortError a failed private-API call stands for (and the channel status)."""
        kind = classify(exc)
        name = type(exc).__name__
        log.warning("instagram channel %s: %s failed: %s (%s)", account.channel_id, what, name,
                    kind.value)
        if kind == IgError.CHALLENGE:
            self.forget(account.channel_id)
            await self.store.set_status(account.channel_id, ChannelStatus.CHALLENGE, RELOGIN)
            return RetryAfter(RELOGIN, SOFT_BLOCK_S)
        if kind == IgError.SOFT_BLOCK:
            return RetryAfter(f"instagram soft block ({name})", SOFT_BLOCK_S)
        if kind == IgError.PERMANENT:
            return PortError(f"instagram refused: {name}")
        return PortError(f"instagram {what} failed: {name}", retryable=True)
