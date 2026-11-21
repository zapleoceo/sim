"""Where a chat stands for the next persona turn: which client messages are still unanswered
and whether their attachments are still being understood (docs/reply.md, «Когда отвечать»).

A client message is answered when a later turn consumed it (`reply_run` sent or blocked with a
trigger event at or after it) or when an outgoing message (persona or operator) followed it."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import ChatEvent, MediaAsset, Message, ReplyRun
from sim.domain.enums import EventKind, RunStatus, UnderstandingStatus

CONSUMED_RUNS = (RunStatus.SENT, RunStatus.BLOCKED)


@dataclass(frozen=True)
class Unanswered:
    last_in_event_id: int
    last_in_at: datetime                    # when the newest unanswered message arrived
    pending_since: datetime | None          # oldest unanswered message still being understood
    count: int


async def answered_until(session: AsyncSession, chat_id: int) -> int:
    run_until = (await session.execute(
        select(func.max(ReplyRun.trigger_event_id))
        .where(ReplyRun.chat_id == chat_id, ReplyRun.status.in_(CONSUMED_RUNS))
    )).scalar_one_or_none()
    out_until = (await session.execute(
        select(func.max(ChatEvent.id))
        .where(ChatEvent.chat_id == chat_id, ChatEvent.kind == EventKind.MESSAGE_OUT)
    )).scalar_one_or_none()
    return max(run_until or 0, out_until or 0)


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
    if not rows:
        return None
    arrivals = [(message.sent_at or event.created_at, media) for event, message, media in rows]
    pending = [at for at, media in arrivals
               if media is not None and media.understanding == UnderstandingStatus.PENDING]
    return Unanswered(
        last_in_event_id=rows[-1][0].id, last_in_at=max(at for at, _ in arrivals),
        pending_since=min(pending) if pending else None, count=len(rows),
    )


async def newer_client_message(session: AsyncSession, chat_id: int, after_event_id: int) -> bool:
    found = (await session.execute(
        select(ChatEvent.id)
        .where(ChatEvent.chat_id == chat_id, ChatEvent.kind == EventKind.MESSAGE_IN,
               ChatEvent.id > after_event_id)
        .limit(1)
    )).scalar_one_or_none()
    return found is not None
