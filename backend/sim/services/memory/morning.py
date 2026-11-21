"""Today's compressed morning (docs/memory.md, task `agent.refresh_memory`). The persona sees her
whole day verbatim; only when the day no longer fits the context budget (rare), the oldest part
of today's window — all but the newest `SIM_CONTEXT_KEEP_RECENT_EVENTS` lines — is folded by the
chronicler into one short «утром…» paragraph (`chat.day_notes[today].morning`, the boundary in
`morning_until`). The old paragraph is part of the new one, so nothing is lost; the boundary
only moves forward. The night summary still reads the whole day verbatim."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sim.db.models import Chat
from sim.db.session import transaction
from sim.domain.enums import EventKind
from sim.domain.memory import DiaryTask, notes_of, with_morning
from sim.events import append_event
from sim.services.context.budget import compression_boundary
from sim.services.context.builder import ContextBuilder
from sim.services.context.diary import day_title
from sim.services.deps import Deps
from sim.services.memory.chronicler import DigestRejected, Writer, ask_chronicler, writer_of

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Fold:
    writer: Writer
    day: date
    old_until: int | None
    boundary: int
    payload: dict[str, object]


class MorningService:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps
        self.builder = ContextBuilder(deps.clock, deps.settings)

    async def refresh(self, chat_id: int) -> bool:
        """True when the morning paragraph was updated."""
        fold = await self._read(chat_id)
        if fold is None:
            return False
        try:
            digest = await ask_chronicler(self.deps, fold.writer, dict(fold.payload))
        except DigestRejected as exc:
            log.warning("chat %s: unusable morning: %s", chat_id, exc)
            return False
        return await self._write(fold, digest.summary)

    async def _read(self, chat_id: int) -> _Fold | None:
        async with transaction(self.deps.sessions) as session:
            know = (await self.builder.reply_frame(session, chat_id)).knowledge
            writer = writer_of(know.persona, chat_id)
            if writer is None:
                return None
            boundary = compression_boundary(know.all_lines, know.timeline_budget,
                                            self.deps.settings.context_keep_recent_events)
            if boundary is None:
                return None
            fold = [line.text for line in know.all_lines if line.event_id <= boundary]
            if not fold:
                return None
            notes = notes_of(know.chat.day_notes, know.today)
            payload = {"task": DiaryTask.MORNING.value, "persona": know.persona.name,
                       "client": know.speakers.client, "date": day_title(know.today),
                       "earlier": notes.morning, "dialog": fold}
            return _Fold(writer=writer, day=know.today,
                         old_until=notes.morning_until, boundary=boundary, payload=payload)

    async def _write(self, fold: _Fold, morning: str) -> bool:
        async with transaction(self.deps.sessions) as session:
            chat = await session.get(Chat, fold.writer.chat_id, with_for_update=True)
            if chat is None:
                return False
            day = fold.day
            current = notes_of(chat.day_notes, day)
            if current.morning_until != fold.old_until:
                return False            # another step got here first
            if fold.old_until is not None and fold.boundary <= fold.old_until:
                return False
            chat.day_notes = with_morning(chat.day_notes, day, morning, fold.boundary)
            await append_event(session, chat_id=chat.id, kind=EventKind.MEMORY_UPDATED,
                               data={"kind": "morning", "day": day.isoformat(),
                                     "morning_until": fold.boundary})
            return True
