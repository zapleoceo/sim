"""Client messages nobody answered: the one definition used by the dialog card, by the recovery
after an AI pause (sim.services.ai_pause) and by the worker's overdue watch.

Unanswered = a client message after the dialog's last outgoing message that was not cancelled.
A message the client only edited (ingest marks it) is a correction, not a new turn."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import Message
from sim.adapters.db.repositories.conversations import MessageRepo
from sim.adapters.db.repositories.traces import TraceRepo
from sim.clock import Clock, utcnow
from sim.services import reply_journal, tracing
from sim.services.dialog_state import turns
from sim.services.reply_journal import ReplyJournal
from sim.services.reply_schedule import ReplyScheduler

WATCH_LOOKBACK = timedelta(hours=24)   # older silence is not news any more


class UnansweredService:
    def __init__(
        self,
        session: AsyncSession,
        journal: ReplyJournal,
        *,
        clock: Clock = utcnow,
        warn_after_s: int = 300,
    ) -> None:
        self.session = session
        self.journal = journal
        self.clock = clock
        self.warn_after = timedelta(seconds=warn_after_s)
        self.messages = MessageRepo(session)
        self.scheduler = ReplyScheduler(session, clock=clock)

    async def of(self, dialog_id: int) -> list[Message]:
        """The dialog's unanswered client messages, oldest first."""
        return turns(await self.messages.unanswered(dialog_id))

    async def warn_overdue(self) -> int:
        """Write one warning per dialog whose oldest unanswered message has waited longer than
        the threshold while the AI is on (paused dialogs have their own journal record; a reply
        delayed by the persona's routine is planned, not lost). The
        same message is never warned about twice. Returns how many warnings were written."""
        now = self.clock()
        waiting: dict[int, list[Message]] = {}
        for message in turns(await self.messages.unanswered_waiting(
                now - WATCH_LOOKBACK, now - self.warn_after)):
            waiting.setdefault(message.dialog_id, []).append(message)
        warned = await TraceRepo(self.session).message_ids(
            tracing.WATCH, reply_journal.OVERDUE, list(waiting))
        written = 0
        for dialog_id, messages in waiting.items():
            first = messages[0]
            if first.id in warned:
                continue
            if await self.scheduler.waits_until(dialog_id) is not None:
                continue    # the answer is planned for later by her routine: not a lost message
            await self.journal.overdue(
                dialog_id, first.id, waiting_s=int((now - first.created_at).total_seconds()),
                threshold_s=int(self.warn_after.total_seconds()), unanswered=len(messages),
            )
            written += 1
        return written
