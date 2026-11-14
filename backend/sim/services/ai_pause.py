"""The AI pause of a dialog (`dialog.ai_paused`): the ONE place that sets it by hand and lifts it.

Lifting the pause recovers what the client wrote meanwhile: one reply job for all unanswered
messages through the usual pipeline (ingest's debounced `reply` job), unless the newest of them
is older than the owner setting «Окно ответа после паузы ИИ» (`pause_recovery_window_min`).
Any new way to lift a pause must go through `AiPauseService.resume`."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import Dialog
from sim.adapters.db.repositories.jobs import JobRepo
from sim.clock import Clock, utcnow
from sim.services import jobkinds
from sim.services.app_settings import AppSettingsService
from sim.services.ingest import IngestService
from sim.services.outbound import cancel_queued_ai
from sim.services.reply_journal import ReplyJournal
from sim.services.unanswered import UnansweredService

NOTHING = "none"        # nobody was waiting for an answer
QUEUED = "queued"       # one reply job was queued for the unanswered messages
STALE = "stale"         # the unanswered messages are too old: left to the operator
REFUSED = "refused"     # the client refused contact: the AI does not write to them


@dataclass(frozen=True)
class Recovery:
    status: str
    messages: int = 0


class AiPauseService:
    def __init__(  # noqa: PLR0913
        self,
        session: AsyncSession,
        ingest: IngestService,
        unanswered: UnansweredService,
        journal: ReplyJournal,
        *,
        clock: Clock = utcnow,
        settings_env: Mapping[str, Any] | None = None,
    ) -> None:
        self.session = session
        self.ingest = ingest
        self.unanswered = unanswered
        self.journal = journal
        self.clock = clock
        self.app_settings = AppSettingsService(session, settings_env)

    async def pause(self, dialog: Dialog) -> None:
        """An operator switched the AI off: replies still waiting for their send do not go out."""
        dialog.ai_paused = True
        await cancel_queued_ai(self.session, dialog.id, "cancelled: AI paused by an operator")

    async def resume(self, dialog: Dialog) -> Recovery:
        """Switch the AI back on and answer what was left unanswered (see the module docstring).
        The caller holds the dialog lock."""
        dialog.ai_paused = False
        dialog.needs_operator = False
        waiting = await self.unanswered.of(dialog.id)
        if not waiting:
            return Recovery(NOTHING)
        newest = waiting[-1]
        window_min = (await self.app_settings.load()).pause_recovery_window_min
        now = self.clock()
        if now - newest.created_at > timedelta(minutes=window_min):
            for message in waiting:
                age_min = int((now - message.created_at).total_seconds() // 60)
                await self.journal.stale(dialog.id, message.id, age_min=age_min, window_min=window_min)
            # A reply job that never ran (the worker was down) must not answer them either.
            await JobRepo(self.session).cancel_pending(jobkinds.REPLY, jobkinds.reply_key(dialog.id))
            return Recovery(STALE, len(waiting))
        if not await self.ingest.queue_reply(dialog, dialog.client, after_pause=True):
            return Recovery(REFUSED, len(waiting))
        await self.journal.recovered(dialog.id, newest.id, unanswered=len(waiting))
        return Recovery(QUEUED, len(waiting))
