"""Journal of replies that were not created: every such decision is a `watch` step of the system
log (Активность → Журнал) and a line of the process log, with the dialog and the message.

Nothing here sends anything or changes a dialog: it only makes the decision visible."""
from __future__ import annotations

import logging
from typing import Any

from sim.services import tracing
from sim.services.tracing import Tracer

log = logging.getLogger(__name__)

PAUSED = "ответ не создан: диалог на паузе ИИ"
STALE = "пропущено, устарело"
RECOVERED = "ответ поставлен после снятия паузы ИИ"
OVERDUE = "входящее без ответа дольше порога"


class ReplyJournal:
    def __init__(self, tracer: Tracer | None = None) -> None:
        self.tracer = tracer

    async def paused(self, dialog_id: int, message_id: int | None, *, source: str) -> None:
        """A client message or a reply job met a dialog whose AI is paused. `source` says which:
        "inbound" (the message was stored, no reply job) or "reply_job" (the job did nothing)."""
        await self.write(PAUSED, tracing.SKIPPED, logging.WARNING, dialog_id, message_id,
                         reason="ai_paused", source=source)

    async def stale(self, dialog_id: int, message_id: int, *, age_min: int, window_min: int) -> None:
        """The pause is over, but this message is older than the recovery window: no auto-reply."""
        await self.write(STALE, tracing.SKIPPED, logging.WARNING, dialog_id, message_id,
                         reason="stale", age_min=age_min, window_min=window_min)

    async def recovered(self, dialog_id: int, message_id: int, *, unanswered: int) -> None:
        """The pause is over and ONE reply job was queued for the messages left unanswered."""
        await self.write(RECOVERED, tracing.OK, logging.INFO, dialog_id, message_id,
                         unanswered=unanswered)

    async def overdue(self, dialog_id: int, message_id: int, *, waiting_s: int, threshold_s: int,
                      unanswered: int) -> None:
        """The AI is on, yet a client message has had no answer for too long."""
        await self.write(OVERDUE, tracing.WARNING, logging.WARNING, dialog_id, message_id,
                         waiting_s=waiting_s, threshold_s=threshold_s, unanswered=unanswered)

    async def write(self, name: str, status: str, level: int, dialog_id: int,
                    message_id: int | None, **detail: Any) -> None:
        log.log(level, "%s (dialog=%s message=%s %s)", name, dialog_id, message_id,
                " ".join(f"{k}={v}" for k, v in detail.items()))
        if self.tracer is not None:
            await self.tracer.record(tracing.WATCH, name, status=status, detail=detail,
                                     dialog_id=dialog_id, message_id=message_id)
