"""The chat journal writer (docs/context.md §1). Every state change of a chat appends one event
in the SAME transaction as the change, through this one function. After commit, listeners
(dashboard SSE) are notified with Postgres NOTIFY on channel `sim_events`."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import ChatEvent
from sim.domain.enums import EventKind

NOTIFY_CHANNEL = "sim_events"

# Kinds the agent does not see by default (operator-only journal lines). An attention decision
# is shown to the agent only when it carries a note for the reply (a missed call she was too
# busy for), so its writer passes `in_context` explicitly.
HIDDEN_FROM_AGENT = frozenset({
    EventKind.GUARD_BLOCKED, EventKind.OPERATOR_NEEDED, EventKind.AI_TOGGLED,
    EventKind.CLIENT_UPDATED, EventKind.MEMORY_UPDATED, EventKind.MEDIA_UNDERSTOOD,
    EventKind.ATTENTION,
})


async def append_event(
    session: AsyncSession, *, chat_id: int, kind: EventKind, actor: str = "system",
    message_id: int | None = None, media_id: int | None = None, call_id: int | None = None,
    generation_id: int | None = None, reply_run_id: int | None = None,
    data: dict[str, Any] | None = None, in_context: bool | None = None,
) -> ChatEvent:
    """`in_context` None = by kind (`HIDDEN_FROM_AGENT`)."""
    visible = kind not in HIDDEN_FROM_AGENT if in_context is None else in_context
    event = ChatEvent(
        chat_id=chat_id, kind=kind, actor=actor, in_context=visible,
        message_id=message_id, media_id=media_id, call_id=call_id,
        generation_id=generation_id, reply_run_id=reply_run_id, data=data or {},
    )
    session.add(event)
    await session.flush()
    # Delivered by Postgres only when the transaction commits; dropped on rollback.
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": NOTIFY_CHANNEL,
         "payload": json.dumps({"chat_id": chat_id, "event_id": event.id, "kind": kind.value})},
    )
    return event
