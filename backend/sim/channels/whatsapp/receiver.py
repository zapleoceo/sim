"""Receiving WhatsApp (docs/channels.md, «Приём»).

Evolution posts every event of an instance to `POST /api/hooks/evolution/{channel_id}` (the
api process, no admin login). The route hands it here:

- the channel must be a WhatsApp channel and the `x-sim-webhook-secret` header must equal the
  secret stored (encrypted) with it — compared in constant time; otherwise 404 / 403;
- a switched-off channel is acknowledged and ignored;
- `messages.upsert` -> each record -> `Inbox.ingest`, exactly like Telegram (own messages,
  groups and broadcasts skipped, sim/channels/whatsapp/parse.py);
- `connection.update`: `open` -> `connected` and a catch-up job; `close` with reason 401 (the
  device was unlinked on the phone) -> `challenge` «нужен повторный вход»; another `close` ->
  `error`.

The catch-up (`channels:whatsapp_catch_up`, the worker) reads the newest client messages of
the instance with `POST /chat/findMessages` and gives the last day's to the Inbox, which skips
what it already has."""
from __future__ import annotations

import hmac
import logging
from datetime import timedelta
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.channels.store import ChannelStore
from sim.channels.whatsapp.evolution import EvolutionTransport, instance_name
from sim.channels.whatsapp.parse import inbound_of, parse_event, records_of, sent_at
from sim.crypto import CryptoError, decrypt_json
from sim.db.models import Channel
from sim.db.session import transaction
from sim.domain.enums import ChannelKind, ChannelStatus
from sim.ports import ClockPort, InboundMessage

log = logging.getLogger(__name__)

CATCH_UP = timedelta(hours=24)
CATCH_UP_LIMIT = 50
LOGGED_OUT = 401
UNLINKED = "WhatsApp отвязан на телефоне: подключите заново"
CLOSED = "WhatsApp отключился, Evolution переподключает"


class HookRefused(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


class Ingest(Protocol):
    async def ingest(self, msg: InboundMessage) -> int | None: ...


class CatchUpQueue(Protocol):
    async def whatsapp_catch_up(self, channel_id: int) -> None: ...


class WhatsAppReceiver:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], secret_key: str,
                 inbox: Ingest, clock: ClockPort, tasks: CatchUpQueue | None = None) -> None:
        self.sessions = sessions
        self.secret_key = secret_key
        self.store = ChannelStore(sessions, secret_key)
        self.inbox = inbox
        self.clock = clock
        self.tasks = tasks

    async def verify(self, channel_id: int, secret: str) -> bool:
        """True = ours and enabled; False = ours but switched off. HookRefused otherwise."""
        async with transaction(self.sessions) as session:
            channel = await session.get(Channel, channel_id)
            if channel is None or channel.kind != ChannelKind.WHATSAPP:
                raise HookRefused(404)
            token, status = channel.credentials_enc or "", channel.status
        try:
            stored = str(decrypt_json(token, self.secret_key).get("webhook_secret") or "")
        except CryptoError:
            stored = ""
        if not stored or not secret or not hmac.compare_digest(stored.encode(),
                                                               secret.encode()):
            raise HookRefused(403)
        return status != ChannelStatus.DISABLED

    async def handle(self, channel_id: int, secret: str, payload: Any) -> int:
        """One webhook call; returns how many new client messages were stored."""
        if not await self.verify(channel_id, secret):
            return 0
        event = parse_event(payload)
        if event.kind == "connection":
            await self.connection(channel_id, event.state, event.reason)
            return 0
        stored = 0
        for record in event.records:
            msg = inbound_of(channel_id, record)
            if msg is not None and await self.inbox.ingest(msg) is not None:
                stored += 1
        return stored

    async def connection(self, channel_id: int, state: str, reason: int | None) -> None:
        if state == "open":
            await self.store.set_status(channel_id, ChannelStatus.CONNECTED, None,
                                        at=self.clock.now())
            if self.tasks is not None:
                await self.tasks.whatsapp_catch_up(channel_id)
        elif state == "close":
            if reason == LOGGED_OUT:
                await self.store.set_status(channel_id, ChannelStatus.CHALLENGE, UNLINKED)
            else:
                await self.store.set_status(channel_id, ChannelStatus.ERROR, CLOSED)
        log.info("whatsapp channel %s: connection %s (%s)", channel_id, state, reason)

    async def catch_up(self, channel_id: int, api: EvolutionTransport) -> int:
        account = await self.store.account(channel_id, ChannelKind.WHATSAPP)
        instance = str(account.credentials.get("instance") or instance_name(channel_id))
        data = await api.request("POST", f"/chat/findMessages/{instance}", {
            "where": {"key": {"fromMe": False}}, "limit": CATCH_UP_LIMIT})
        since = self.clock.now() - CATCH_UP
        records = sorted(records_of(data), key=lambda r: sent_at(r.get("messageTimestamp")))
        stored = 0
        for record in records:
            msg = inbound_of(channel_id, record)
            if msg is None or msg.sent_at < since:
                continue
            if await self.inbox.ingest(msg) is not None:
                stored += 1
        if stored:
            log.info("whatsapp channel %s: %s messages caught up", channel_id, stored)
        return stored
