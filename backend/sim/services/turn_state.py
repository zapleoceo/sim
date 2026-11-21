"""Where a chat stands for the next persona turn: which client messages are still unanswered
and whether their attachments are still being understood (docs/reply.md, «Когда отвечать»).

A client message is answered when a later turn consumed it (`reply_run` sent or blocked with a
trigger event at or after it) or when an outgoing message (persona or operator) followed it.
A call she did not take because she was asleep or busy (`call` event with `life_busy`) waits
for an answer the same way: she writes back when she is free."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, ChatEvent, Client, MediaAsset, Message, Persona, ReplyRun
from sim.domain.enums import EventKind, RunStatus, UnderstandingStatus

CONSUMED_RUNS = (RunStatus.SENT, RunStatus.BLOCKED)
# `chat_event.data` keys of a call she did not take because of her life (docs/attention.md):
# her busy level then, and what she was doing.
LIFE_REFUSED_KEY = "life_busy"
LIFE_ACTIVITY_KEY = "life_activity"


def ai_allowed(persona: Persona | None, client: Client | None, chat: Chat) -> bool:
    """May `persona` answer in `chat`? It must be the chat's OWN persona (`chat.persona_id`,
    never another one), active (not archived), and AI must be on for her, the client and the
    chat. The one rule for incoming messages, replies and calls."""
    return bool(persona and client and persona.id == chat.persona_id and persona.is_active
                and persona.ai_enabled and client.ai_enabled and chat.ai_enabled)


@dataclass(frozen=True)
class Unanswered:
    last_in_event_id: int
    last_in_at: datetime                    # when the newest unanswered message arrived
    pending_since: datetime | None          # oldest unanswered message still being understood
    count: int
    last_in_message_id: int | None = None   # the newest unanswered message (read receipt)
    first_in_at: datetime | None = None     # when the oldest unanswered one arrived


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
