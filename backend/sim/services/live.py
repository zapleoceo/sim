"""Live updates for the admin: the inbox refreshes the moment a dialog changes, without polling.

How a change travels:
1. Any transaction that adds / changes / deletes a `Message` or changes a `Dialog` (ingest in
   the worker, a persona reply, a send receipt, an operator action, `/reset`) is noticed by
   SQLAlchemy session events (`track_dialog_changes`), in whichever process it runs.
2. On PostgreSQL the transaction itself sends `pg_notify('sim_live', '{"dialogs": [ids]}')`,
   delivered only if it commits (worker and API are separate processes). Elsewhere (SQLite in
   tests / local dev) the ids are published straight to this process's hub after the commit.
3. The API process listens on the channel (`listen`) and fans the ids out to every open
   `GET /api/events` stream (`LiveHub`); the admin turns them into query refreshes.

Only dialog ids travel: no texts, names or other data, so an event never leaks content; the
page re-reads what it may see through the normal authenticated API."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Iterable
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import Session

from sim.adapters.db.models import Dialog, Message

log = logging.getLogger(__name__)

CHANNEL = "sim_live"
MAX_IDS = 200                 # pg_notify payloads are capped at 8000 bytes
QUEUE_SIZE = 100              # a stalled browser drops events, it never blocks the hub
PENDING = "sim_live_dialogs"  # Session.info key


class LiveHub:
    """Process-local fan-out of changed dialog ids to the open event streams."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[list[int]]] = set()

    def publish(self, dialog_ids: Iterable[int]) -> None:
        ids = sorted({int(i) for i in dialog_ids})[:MAX_IDS]
        if not ids:
            return
        for queue in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(ids)

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[list[int]]]:
        queue: asyncio.Queue[list[int]] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    @property
    def subscribers(self) -> int:
        return len(self._subscribers)


HUB = LiveHub()


def changed_dialog_ids(session: Session) -> set[int]:
    """Dialogs touched by the pending flush (in `after_flush` new rows already have ids)."""
    ids: set[int] = set()
    for obj in (*session.new, *session.dirty, *session.deleted):
        if isinstance(obj, Message) and obj.dialog_id:
            ids.add(int(obj.dialog_id))
        elif isinstance(obj, Dialog) and obj.id:
            ids.add(int(obj.id))
    return ids


def _after_flush(session: Session, _flush_context: Any) -> None:
    if ids := changed_dialog_ids(session):
        session.info.setdefault(PENDING, set()).update(ids)


def _is_postgres(session: Session) -> bool:
    return session.get_bind().dialect.name == "postgresql"


def _before_commit(session: Session) -> None:
    ids = session.info.get(PENDING)
    if ids and _is_postgres(session):
        payload = json.dumps({"dialogs": sorted(ids)[:MAX_IDS]})
        session.execute(text("SELECT pg_notify(:c, :p)"), {"c": CHANNEL, "p": payload})


def _after_commit(session: Session) -> None:
    ids = session.info.pop(PENDING, None)
    if ids and not _is_postgres(session):
        HUB.publish(ids)          # no NOTIFY outside PostgreSQL: this process only


def _after_rollback(session: Session, _previous: Any) -> None:
    session.info.pop(PENDING, None)


_installed = False


def track_dialog_changes() -> None:
    """Install the session hooks once per process (idempotent)."""
    global _installed
    if _installed:
        return
    event.listen(Session, "after_flush", _after_flush)
    event.listen(Session, "before_commit", _before_commit)
    event.listen(Session, "after_commit", _after_commit)
    event.listen(Session, "after_soft_rollback", _after_rollback)
    _installed = True


@contextlib.asynccontextmanager
async def listen(engine: AsyncEngine) -> AsyncIterator[None]:
    """API process: forward PostgreSQL notifications to the hub while the app runs. A no-op on
    other databases (the hub is fed directly there)."""
    if engine.dialect.name != "postgresql":
        yield
        return
    conn = await engine.connect()
    raw = (await conn.get_raw_connection()).driver_connection

    def on_notify(_conn: Any, _pid: int, _channel: str, payload: str) -> None:
        try:
            HUB.publish(json.loads(payload).get("dialogs") or [])
        except (ValueError, AttributeError, TypeError):
            log.warning("bad live payload ignored")

    await raw.add_listener(CHANNEL, on_notify)
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            await raw.remove_listener(CHANNEL, on_notify)
        await conn.close()
