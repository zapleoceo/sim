"""One Telethon client factory and the runtime that keeps every Telegram account connected.

`build_client` is the ONLY place that creates a TelegramClient: StringSession, proxy and the
client flags live here. `ChannelRuntime` loads every `telegram_user` channel that is not disabled,
decrypts its credentials, connects, records the outcome on the channel row (status, last_error,
account_id, account_label) and reconnects with a growing pause when the connection drops. A dead
session (revoked, logged out, banned) is NOT retried: it needs a new login by the owner.

Other parts of the process (receiver, calls) attach themselves through `hooks`: each hook gets
`(channel_id, client)` every time a fresh client is connected."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.db.models import Channel
from sim.db.session import transaction
from sim.domain.enums import ChannelKind, ChannelStatus
from sim.ports import PortError

log = logging.getLogger(__name__)

BACKOFF_S = (2.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0)

_PROXY_TYPES = {"socks5": "socks5", "socks5h": "socks5", "socks4": "socks4", "http": "http",
                "https": "http"}

# Telethon error class names meaning "this session is dead" (as opposed to a network blip).
DEAD_SESSION_ERRORS = frozenset({
    "AuthKeyUnregisteredError", "SessionRevokedError", "SessionExpiredError",
    "UserDeactivatedError", "UserDeactivatedBanError", "AuthKeyDuplicatedError",
    "AuthKeyInvalidError", "SessionPasswordNeededError",
})

Hook = Callable[[int, Any], Awaitable[None]]
Decrypt = Callable[[str, str], dict[str, Any]]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class Credentials:
    """The decrypted `channel.credentials_enc` JSON. Never logged."""

    api_id: int
    api_hash: str
    session: str
    proxy: str = ""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Credentials:
        try:
            api_id = int(data.get("api_id") or 0)
        except (TypeError, ValueError):
            api_id = 0
        api_hash = str(data.get("api_hash") or "").strip()
        session = str(data.get("session") or "").strip()
        if api_id <= 0 or not api_hash or not session:
            raise ValueError("credentials need api_id, api_hash and session")
        return cls(api_id, api_hash, session, str(data.get("proxy") or "").strip())


ClientFactory = Callable[[Credentials], Any]


def parse_proxy(url: str) -> dict[str, Any] | None:
    """`scheme://user:pass@host:port` (scheme defaults to socks5) -> Telethon proxy dict."""
    raw = (url or "").strip()
    if not raw:
        return None
    parts = urlsplit(raw if "://" in raw else f"socks5://{raw}")
    kind = _PROXY_TYPES.get(parts.scheme.lower())
    try:
        port = parts.port
    except ValueError:
        port = None
    if kind is None or not parts.hostname or not port:
        raise ValueError("proxy must look like socks5://user:pass@host:port")
    proxy: dict[str, Any] = {"proxy_type": kind, "addr": parts.hostname, "port": port,
                             "rdns": True}
    if parts.username:
        proxy["username"] = unquote(parts.username)
        proxy["password"] = unquote(parts.password or "")
    return proxy


def build_client(credentials: Credentials, *, receive_updates: bool = True) -> Any:
    """A Telethon client of a personal account.

    flood_sleep_threshold=0: Telethon would otherwise sleep through short FloodWaits inside any
    call; we surface them (FloodWaitError) so the send task retries visibly and nothing in the
    process hangs. Telethon reconnects on its own (auto_reconnect); the runtime handles the case
    when it gives up."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    return TelegramClient(
        StringSession(credentials.session), credentials.api_id, credentials.api_hash,
        proxy=parse_proxy(credentials.proxy), receive_updates=receive_updates,
        flood_sleep_threshold=0, connection_retries=3, request_retries=3, auto_reconnect=True,
    )


def account_label(me: Any) -> str:
    username = getattr(me, "username", None)
    if username:
        return f"@{username}"
    phone = str(getattr(me, "phone", "") or "").lstrip("+")
    if phone:
        return f"+{phone}"
    return str(getattr(me, "first_name", "") or "Telegram")


def is_dead_session(exc: BaseException) -> bool:
    return type(exc).__name__ in DEAD_SESSION_ERRORS


def short_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:300]}"


class DeadSession(Exception):
    """The account cannot work until the owner logs in again."""


class ChannelRuntime:
    """Connected Telethon clients of all Telegram channels of this process."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, secret_key: str,
        hooks: Sequence[Hook] = (), client_factory: ClientFactory = build_client,
        decrypt: Decrypt | None = None, sleep: Sleep = asyncio.sleep,
        backoff_s: Sequence[float] = BACKOFF_S,
    ) -> None:
        self.sessions = sessions
        self.secret_key = secret_key
        self.hooks = list(hooks)
        self.factory = client_factory
        self.decrypt = decrypt
        self.sleep = sleep
        self.backoff_s = tuple(backoff_s)
        self.clients: dict[int, Any] = {}
        self.tasks: dict[int, asyncio.Task[None]] = {}
        self.stopping = False

    # --- lifecycle -----------------------------------------------------------------------

    async def start(self) -> list[int]:
        """Starts a supervisor per channel; returns the channel ids it started.

        Safe to call again (the process does it every minute): it picks up channels added or
        re-enabled since. A supervisor that gave up on a dead session left the channel in
        `error`; it is restarted only after a fresh login moves the channel out of `error`, so
        a dead session is not hammered."""
        async with transaction(self.sessions) as session:
            rows = (await session.execute(
                select(Channel.id, Channel.status)
                .where(Channel.kind == ChannelKind.TELEGRAM_USER,
                       Channel.status != ChannelStatus.DISABLED)
                .order_by(Channel.id))).all()
        started: list[int] = []
        for channel_id, status in rows:
            task = self.tasks.get(channel_id)
            if task is not None and (not task.done() or status == ChannelStatus.ERROR):
                continue
            self.tasks[channel_id] = asyncio.create_task(
                self.supervise(channel_id), name=f"telegram-channel-{channel_id}")
            started.append(channel_id)
        return started

    async def stop(self) -> None:
        self.stopping = True
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()
        for client in list(self.clients.values()):
            await self.disconnect(client)
        self.clients.clear()

    def client(self, channel_id: int) -> Any:
        """The connected client, or a retryable PortError while it is (re)connecting."""
        client = self.clients.get(channel_id)
        if client is None or not client.is_connected():
            raise PortError(f"telegram channel {channel_id} is not connected", retryable=True)
        return client

    # --- one channel ---------------------------------------------------------------------

    async def supervise(self, channel_id: int) -> None:
        """Connect, serve until disconnected, pause, reconnect; stop on a dead session."""
        attempt = 0
        while not self.stopping:
            try:
                served = await self.serve(channel_id)
            except DeadSession as exc:
                await self.set_status(channel_id, ChannelStatus.ERROR, str(exc))
                log.warning("telegram channel %s needs a new login", channel_id)
                return
            except Exception as exc:
                if is_dead_session(exc):
                    await self.set_status(channel_id, ChannelStatus.ERROR,
                                          f"session is no longer valid: {short_error(exc)}")
                    log.warning("telegram channel %s session is dead: %s", channel_id,
                                type(exc).__name__)
                    return
                await self.set_status(channel_id, ChannelStatus.ERROR, short_error(exc))
                log.warning("telegram channel %s failed: %s", channel_id, type(exc).__name__)
                served = False
            if served is None:
                return                               # disabled or removed meanwhile
            attempt = 0 if served else attempt + 1
            delay = self.backoff_s[min(attempt, len(self.backoff_s) - 1)]
            await self.sleep(delay)

    async def serve(self, channel_id: int) -> bool | None:
        """One connection. True = it was up and then dropped; None = nothing to run."""
        credentials = await self.load(channel_id)
        if credentials is None:
            return None
        client = self.factory(credentials)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise DeadSession("Telegram session is not authorized: log in again")
            me = await client.get_me()
            self.clients[channel_id] = client
            await self.set_status(channel_id, ChannelStatus.CONNECTED, None, me=me)
            for hook in self.hooks:
                try:
                    await hook(channel_id, client)
                except Exception:
                    log.exception("telegram channel %s: hook failed", channel_id)
            log.info("telegram channel %s connected", channel_id)
            await client.disconnected
            await self.set_status(channel_id, ChannelStatus.ERROR, "disconnected")
            return True
        finally:
            if self.clients.get(channel_id) is client:
                del self.clients[channel_id]
            await self.disconnect(client)

    async def load(self, channel_id: int) -> Credentials | None:
        async with transaction(self.sessions) as session:
            channel = await session.get(Channel, channel_id)
            if channel is None or channel.status == ChannelStatus.DISABLED:
                return None
            token = channel.credentials_enc
        if not token:
            raise DeadSession("no credentials: connect the account first")
        try:
            return Credentials.from_json(self.decrypt_fn()(token, self.secret_key))
        except Exception as exc:
            raise DeadSession(f"credentials are unreadable: {type(exc).__name__}") from None

    def decrypt_fn(self) -> Decrypt:
        if self.decrypt is None:
            from sim.crypto import decrypt_json

            self.decrypt = decrypt_json
        return self.decrypt

    async def set_status(
        self, channel_id: int, status: ChannelStatus, error: str | None, *, me: Any = None,
    ) -> None:
        try:
            async with transaction(self.sessions) as session:
                channel = await session.get(Channel, channel_id)
                if channel is None or channel.status == ChannelStatus.DISABLED:
                    return
                channel.status = status
                channel.last_error = error
                if me is not None:
                    channel.account_id = str(getattr(me, "id", "") or "") or None
                    channel.account_label = account_label(me)
        except Exception:
            log.exception("telegram channel %s: status not saved", channel_id)

    @staticmethod
    async def disconnect(client: Any) -> None:
        with contextlib.suppress(Exception):
            await client.disconnect()
