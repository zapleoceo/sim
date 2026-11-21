"""Procrastinate tasks of the `channels` queue — Instagram Direct and WhatsApp — run by the
worker process (sim/apps/worker.py). Nothing here holds a connection while no Instagram /
WhatsApp channel exists: the runtime (clients, presence) is made by the first job that needs it,
an Instagram client only for a connected account, and Evolution is reached per request.

  channels:send_message(message_id)   the shared delivery (sim/channels/delivery.py),
                                       `exclusive`: claimed, sent and marked in one transaction
  channels:mark_read(message_id)      «прочитано» (Instagram «Просмотрено», WhatsApp blue ticks)
  channels:chat_action(chat_id, ...)  WhatsApp «печатает…» / «записывает аудио…»
  channels:instagram_tick             every minute: queue the polls of the accounts that are due
  channels:poll_instagram(channel_id) one poll of an account's Direct (sim/channels/instagram)
  channels:whatsapp_catch_up(channel) what a WhatsApp account got while offline

Registered under these exact names in every process (`register_channel_tasks`), so the API and
the worker defer them by name."""
from __future__ import annotations

import logging
from typing import Any

import procrastinate
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.channels.delivery import (
    MAX_RETRIES,
    SendRetry,
    acknowledge,
    attempts_of,
    deliver,
    request_presence,
)
from sim.channels.instagram.account import InstagramAccounts
from sim.channels.instagram.messenger import InstagramMessenger
from sim.channels.instagram.poller import InstagramPoller, PollSchedule
from sim.channels.store import ChannelStore
from sim.channels.whatsapp.evolution import EvolutionTransport, HttpEvolution
from sim.channels.whatsapp.messenger import WhatsAppMessenger
from sim.channels.whatsapp.receiver import WhatsAppReceiver
from sim.db.models import Channel
from sim.db.session import transaction
from sim.domain.enums import ChannelKind
from sim.ports import MessengerPort, OutboundMessage, Peer, PortError
from sim.tasks.queue import (
    CHANNELS_CHAT_ACTION_TASK,
    CHANNELS_MARK_READ_TASK,
    CHANNELS_QUEUE,
    CHANNELS_SEND_TASK,
    IG_POLL_TASK,
    IG_TICK_CRON,
    IG_TICK_TASK,
    WA_CATCH_UP_TASK,
)
from sim.telegram.presence import Presence

log = logging.getLogger(__name__)


class DispatchMessenger:
    """One `MessengerPort` over the channels of this queue: picks the messenger of the
    channel's kind (a channel never changes its kind, so it is cached)."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession],
                 messengers: dict[ChannelKind, MessengerPort]) -> None:
        self.sessions = sessions
        self.messengers = messengers
        self._kinds: dict[int, ChannelKind] = {}

    async def _of(self, channel_id: int) -> MessengerPort:
        kind = self._kinds.get(channel_id)
        if kind is None:
            async with transaction(self.sessions) as session:
                channel = await session.get(Channel, channel_id)
            if channel is None:
                raise PortError(f"channel {channel_id} not found")
            kind = self._kinds[channel_id] = ChannelKind(channel.kind)
        messenger = self.messengers.get(kind)
        if messenger is None:
            raise PortError(f"{kind} is not served here (WhatsApp: Evolution not configured)")
        return messenger

    async def send(self, channel_id: int, message: OutboundMessage) -> str | None:
        return await (await self._of(channel_id)).send(channel_id, message)

    async def set_typing(self, channel_id: int, peer: Peer, action: str) -> None:
        await (await self._of(channel_id)).set_typing(channel_id, peer, action)

    async def mark_read(self, channel_id: int, peer: Peer, external_id: str) -> None:
        await (await self._of(channel_id)).mark_read(channel_id, peer, external_id)


class ChannelsRuntime:
    """Everything the `channels` jobs of this process share, built from `Deps`."""

    def __init__(self, deps: Any, *, evolution: EvolutionTransport | None = None,
                 accounts: InstagramAccounts | None = None, poller: Any = None) -> None:
        settings = deps.settings
        self.deps = deps
        self.store = ChannelStore(deps.sessions, settings.secret_key)
        self.accounts = accounts or InstagramAccounts(
            self.store, deps.clock, hourly_cap=settings.ig_hourly_cap,
            daily_cap=settings.ig_daily_cap, default_proxy=settings.ig_default_proxy)
        if evolution is None and settings.whatsapp_ready:
            evolution = HttpEvolution(settings.evolution_url, settings.evolution_api_key)
        self.evolution = evolution
        messengers: dict[ChannelKind, MessengerPort] = {
            ChannelKind.INSTAGRAM: InstagramMessenger(self.accounts)}
        if evolution is not None:
            messengers[ChannelKind.WHATSAPP] = WhatsAppMessenger(evolution, self.store)
        self.messenger = DispatchMessenger(deps.sessions, messengers)
        self.presence = Presence(self.messenger)
        inbox = _inbox(deps)
        self.poller = poller or InstagramPoller(self.accounts, inbox, deps.clock)
        self.schedule = PollSchedule(settings.ig_poll_interval_s, deps.uniform)
        self.receiver = WhatsAppReceiver(deps.sessions, settings.secret_key, inbox, deps.clock,
                                         deps.tasks)


def _inbox(deps: Any) -> Any:
    from sim.services.inbox import Inbox

    return Inbox(deps)


_registry: dict[str, ChannelsRuntime] = {}


def set_runtime(runtime: ChannelsRuntime | None) -> None:
    """Tests (and nothing else) put their own runtime here; None forgets it."""
    _registry.pop("runtime", None)
    if runtime is not None:
        _registry["runtime"] = runtime


def get_runtime() -> ChannelsRuntime:
    runtime = _registry.get("runtime")
    if runtime is None:
        from sim.services.deps import get_deps

        runtime = _registry["runtime"] = ChannelsRuntime(get_deps())
    return runtime


def _outbox() -> Any:
    from sim.services import outbox

    return outbox


# --- tasks -----------------------------------------------------------------------------------


async def send_message(context: procrastinate.JobContext, message_id: int) -> None:
    runtime = get_runtime()
    deps = runtime.deps
    await deliver(
        message_id, sessions=deps.sessions, storage=deps.storage, clock=deps.clock,
        messenger=runtime.messenger, outbox=_outbox(),
        final_attempt=attempts_of(context) >= MAX_RETRIES, presence=runtime.presence,
        exclusive=True,
    )


async def mark_read(message_id: int) -> None:
    runtime = get_runtime()
    try:
        await acknowledge(message_id, sessions=runtime.deps.sessions,
                          messenger=runtime.messenger)
    except PortError as exc:
        log.info("message %s: mark read skipped: %s", message_id, exc)


async def chat_action(chat_id: int, action: str, seconds: float) -> None:
    runtime = get_runtime()
    try:
        await request_presence(chat_id, action, seconds, sessions=runtime.deps.sessions,
                               presence=runtime.presence)
    except PortError as exc:
        log.info("chat %s: chat action skipped: %s", chat_id, exc)


async def instagram_tick(timestamp: int) -> None:
    """Every minute: the connected Instagram accounts that are due get a poll (with jitter).
    Without Instagram channels it is one query and nothing else."""
    runtime = get_runtime()
    channel_ids = await runtime.store.connected(ChannelKind.INSTAGRAM)
    for channel_id, delay_s in runtime.schedule.due(channel_ids, runtime.deps.clock.now()):
        await runtime.deps.tasks.poll_instagram(channel_id, delay_s)


async def poll_instagram(channel_id: int) -> None:
    count = await get_runtime().poller.poll(channel_id)
    if count:
        log.info("instagram channel %s: %s new messages", channel_id, count)


async def whatsapp_catch_up(channel_id: int) -> None:
    runtime = get_runtime()
    if runtime.evolution is None:
        log.info("whatsapp channel %s: no catch-up, Evolution is not configured", channel_id)
        return
    try:
        await runtime.receiver.catch_up(channel_id, runtime.evolution)
    except PortError as exc:
        log.info("whatsapp channel %s: catch-up skipped: %s", channel_id, exc)


def register_channel_tasks(app: procrastinate.App) -> None:
    app.task(name=CHANNELS_SEND_TASK, queue=CHANNELS_QUEUE, retry=SendRetry(),
             pass_context=True)(send_message)
    app.task(name=CHANNELS_MARK_READ_TASK, queue=CHANNELS_QUEUE)(mark_read)
    app.task(name=CHANNELS_CHAT_ACTION_TASK, queue=CHANNELS_QUEUE)(chat_action)
    tick = app.task(name=IG_TICK_TASK, queue=CHANNELS_QUEUE)(instagram_tick)
    app.periodic(cron=IG_TICK_CRON, periodic_id="instagram")(tick)
    app.task(name=IG_POLL_TASK, queue=CHANNELS_QUEUE)(poll_instagram)
    app.task(name=WA_CATCH_UP_TASK, queue=CHANNELS_QUEUE)(whatsapp_catch_up)
