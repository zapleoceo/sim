"""Where a chat stands for the next persona turn: which client messages are still unanswered
and whether their attachments are still being understood (docs/reply.md, «Когда отвечать»).

A client message is answered when a later turn consumed it (`reply_run` sent or blocked with a
trigger event at or after it) or when an outgoing message (persona or operator) followed it.
A call she did not take because she was asleep or busy (`call` event with `life_busy`) waits
for an answer the same way: she writes back when she is free."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import ChatEvent, MediaAsset, Message, ReplyRun
from sim.domain.enums import EventKind, RunStatus, UnderstandingStatus

CONSUMED_RUNS = (RunStatus.SENT, RunStatus.BLOCKED)
# `chat_event.data` keys of a call she did not take because of her life (docs/attention.md):
# her busy level then, and what she was doing.
LIFE_REFUSED_KEY = "life_busy"
LIFE_ACTIVITY_KEY = "life_activity"


@dataclass(frozen=True)
class Unanswered:
    last_in_event_id: int
    last_in_at: datetime                    # when the newest unanswered message arrived
    pending_since: datetime | None          # oldest unanswered message still being understood
    count: int
    last_in_message_id: int | None = None   # the newest unanswered message (read receipt)
    first_in_at: datetime | None = None     # when the oldest unanswered one arrived


@dataclass(frozen=True)
class Waiting:
    """How many events of a chat wait for her answer, and the newest of them."""

    count: int
    last_event_id: int


async def answered_bounds(session: AsyncSession, chat_ids: Sequence[int]) -> dict[int, int]:
    """`answered_until` of many chats in two queries (0 = nothing answered yet)."""
    if not chat_ids:
        return {}
    runs = await session.execute(
        select(ReplyRun.chat_id, func.max(ReplyRun.trigger_event_id))
        .where(ReplyRun.chat_id.in_(chat_ids), ReplyRun.status.in_(CONSUMED_RUNS))
        .group_by(ReplyRun.chat_id))
    outs = await session.execute(
        select(ChatEvent.chat_id, func.max(ChatEvent.id))
        .where(ChatEvent.chat_id.in_(chat_ids), ChatEvent.kind == EventKind.MESSAGE_OUT)
        .group_by(ChatEvent.chat_id))
    bounds = dict.fromkeys(chat_ids, 0)
    for chat_id, until in [*runs.all(), *outs.all()]:
        bounds[chat_id] = max(bounds[chat_id], until or 0)
    return bounds


async def answered_until(session: AsyncSession, chat_id: int) -> int:
    return (await answered_bounds(session, [chat_id]))[chat_id]


async def waiting_counts(session: AsyncSession,
                         bounds: Mapping[int, int]) -> dict[int, Waiting]:
    """Per chat: the client messages and the calls she did not take because of her life after
    its answered boundary (`answered_bounds`), in one query. A chat with none is absent."""
    if not bounds:
        return {}
    after = or_(*(and_(ChatEvent.chat_id == chat_id, ChatEvent.id > until)
                  for chat_id, until in bounds.items()))
    waits = or_(ChatEvent.kind == EventKind.MESSAGE_IN,
                and_(ChatEvent.kind == EventKind.CALL, ChatEvent.data.has_key(LIFE_REFUSED_KEY)))
    rows = await session.execute(
        select(ChatEvent.chat_id, func.count(), func.max(ChatEvent.id))
        .where(after, waits).group_by(ChatEvent.chat_id))
    return {chat_id: Waiting(count=count, last_event_id=last)
            for chat_id, count, last in rows.all()}


async def unanswered(session: AsyncSession, chat_id: int) -> Unanswered | None:
    boundary = await answered_until(session, chat_id)
    rows = (await session.execute(
        select(ChatEvent, Message, MediaAsset)
        .join(Message, Message.id == ChatEvent.message_id)
        .outerjoin(MediaAsset, MediaAsset.id == Message.media_id)
        .where(ChatEvent.chat_id == chat_id, ChatEvent.kind == EventKind.MESSAGE_IN,
               ChatEvent.id > boundary)
        .order_by(ChatEvent.id)
    )).all()
    # A call she did not take because of her life (asleep, busy) also waits for a text.
    calls = list((await session.scalars(
        select(ChatEvent)
        .where(ChatEvent.chat_id == chat_id, ChatEvent.kind == EventKind.CALL,
               ChatEvent.id > boundary, ChatEvent.data.has_key(LIFE_REFUSED_KEY))
        .order_by(ChatEvent.id)
    )).all())
    if not rows and not calls:
        return None
    arrivals = [(message.sent_at or event.created_at, media) for event, message, media in rows]
    arrivals += [(event.created_at, None) for event in calls]
    pending = [at for at, media in arrivals
               if media is not None and media.understanding == UnderstandingStatus.PENDING]
    event_ids = [event.id for event, _, _ in rows]
    event_ids += [event.id for event in calls]
    return Unanswered(
        last_in_event_id=max(event_ids), last_in_message_id=rows[-1][1].id if rows else None,
        last_in_at=max(at for at, _ in arrivals), first_in_at=min(at for at, _ in arrivals),
        pending_since=min(pending) if pending else None, count=len(rows) + len(calls),
    )


async def newer_client_message(session: AsyncSession, chat_id: int, after_event_id: int) -> bool:
    found = (await session.execute(
        select(ChatEvent.id)
        .where(ChatEvent.chat_id == chat_id, ChatEvent.kind == EventKind.MESSAGE_IN,
               ChatEvent.id > after_event_id)
        .limit(1)
    )).scalar_one_or_none()
    return found is not None
