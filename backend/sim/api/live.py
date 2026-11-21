"""Live dashboard updates: ONE Postgres `LISTEN sim_events` per API process, fanned out to every
open SSE stream through in-memory queues. `sim.events.append_event` sends the NOTIFY; the
payload (`{"chat_id", "event_id", "kind"}`) is passed through to the browser unchanged."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import psycopg
from psycopg import sql

from sim.events import NOTIFY_CHANNEL

logger = logging.getLogger(__name__)

HEARTBEAT_S = 20.0      # SSE comment line that keeps nginx / browsers from closing an idle stream
RETRY_MS = 3000         # browser reconnect delay after a drop
QUEUE_SIZE = 256        # per subscriber; a reader this far behind loses the oldest events
RECONNECT_S = 3.0

Publish = Callable[[str], None]
Listener = Callable[[Publish], Coroutine[Any, Any, None]]


def plain_dsn(database_url: str) -> str:
    """`postgresql+psycopg://…` (SQLAlchemy URL) -> `postgresql://…` (libpq DSN)."""
    return re.sub(r"^postgresql\+\w+://", "postgresql://", database_url)


def pg_listener(database_url: str, channel: str = NOTIFY_CHANNEL, *,
                reconnect_s: float = RECONNECT_S) -> Listener:
    """A listener that holds one autocommit psycopg connection, LISTENs on `channel` and
    publishes every payload; it reconnects forever until cancelled."""
    dsn = plain_dsn(database_url)

    async def listen(publish: Publish) -> None:
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
                    await conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(channel)))
                    async for note in conn.notifies():
                        publish(note.payload)
            except Exception:
                logger.exception("LISTEN %s dropped, reconnecting", channel)
            await asyncio.sleep(reconnect_s)

    return listen


class EventHub:
    """Fan-out of NOTIFY payloads to subscriber queues. The listener starts with the first
    subscriber (so a process that never serves SSE opens no extra connection)."""

    def __init__(self, listen: Listener) -> None:
        self._listen = listen
        self._queues: set[asyncio.Queue[str]] = set()
        self._task: asyncio.Task[None] | None = None

    @property
    def subscriber_count(self) -> int:
        return len(self._queues)

    def publish(self, payload: str) -> None:
        for queue in list(self._queues):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(payload)

    def add_subscriber(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._queues.add(queue)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._listen(self.publish), name="sim-events-listen")
        return queue

    def remove_subscriber(self, queue: asyncio.Queue[str]) -> None:
        self._queues.discard(queue)

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[str]]:
        queue = self.add_subscriber()
        try:
            yield queue
        finally:
            self.remove_subscriber(queue)

    async def close(self, _app: object = None) -> None:
        """Stop the listener (app shutdown hook)."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def format_event(payload: str) -> str:
    return "".join(f"data: {line}\n" for line in payload.splitlines() or [""]) + "\n"


async def sse_stream(hub: EventHub, *, heartbeat_s: float = HEARTBEAT_S) -> AsyncIterator[str]:
    """The text of one SSE response: retry hint, then every event, with heartbeat comments."""
    queue = hub.add_subscriber()
    try:
        yield f"retry: {RETRY_MS}\n\n"
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            yield format_event(payload)
    finally:
        hub.remove_subscriber(queue)
