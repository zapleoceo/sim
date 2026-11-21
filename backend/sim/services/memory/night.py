"""The night job (docs/memory.md): at the persona's local midnight, every chat that had events
that day gets the chronicler's day summary and then the passport writer's update from that
day; the night to Monday also rolls the week's days up into a week summary.

- Task `agent.memory_tick` (every 15 min) asks `due()`: for every active persona whose
  chronicler has a model, the last `LOOKBACK_DAYS` finished days that still have a chat to
  summarize (or, on a Sunday, a week to roll up). Task `agent.night_memory(persona, day)` runs
  `run()`; one job per persona and day (queueing lock), so the first tick after midnight does
  the work and later ticks find nothing due.
- Idempotent per chat and day: a summarized day (or one the owner edited) is skipped; a failed
  one (an error row) is tried again after `RETRY_AFTER`."""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, ChatDay, ChatEvent, ChatWeek, Persona
from sim.db.session import transaction
from sim.domain.agents import AgentKey, agent_config
from sim.domain.enums import DaySource
from sim.ports import PortError
from sim.services.context.diary import usable
from sim.services.context.window import day_bounds, local_day, week_monday
from sim.services.deps import Deps
from sim.services.memory.chronicler import Chronicler, Outcome
from sim.services.passport import PassportService

log = logging.getLogger(__name__)

LOOKBACK_DAYS = 2               # a missed night (downtime) is caught up the next night
RETRY_AFTER = timedelta(hours=3)
SUNDAY = 6


@dataclass
class NightResult:
    days: dict[int, Outcome] = field(default_factory=dict)        # chat -> outcome
    passports: list[int] = field(default_factory=list)            # chats whose passport changed
    weeks: dict[int, Outcome] = field(default_factory=dict)


def done(row: ChatDay | ChatWeek | None, now: datetime) -> bool:
    """Nothing to do now: summarized, edited by the owner, or failed less than
    `RETRY_AFTER` ago."""
    if row is None:
        return False
    failed_recently = bool(row.error) and now - row.updated_at < RETRY_AFTER
    return row.source == DaySource.EDITED or usable(row) or failed_recently


async def chats_to_summarize(session: AsyncSession, persona: Persona, day: date,
                             now: datetime) -> list[int]:
    """Chats of the persona with events the agent sees on her `day`, not done yet."""
    start, end = day_bounds(day, persona.timezone)
    active = set(await session.scalars(
        select(ChatEvent.chat_id).distinct().join(Chat, Chat.id == ChatEvent.chat_id)
        .where(Chat.persona_id == persona.id, ChatEvent.in_context.is_(True),
               ChatEvent.created_at >= start, ChatEvent.created_at < end)))
    if not active:
        return []
    rows = await session.scalars(select(ChatDay).where(ChatDay.chat_id.in_(active),
                                                       ChatDay.day == day))
    finished = {row.chat_id for row in rows if done(row, now)}
    return sorted(active - finished)


async def chats_to_roll_up(session: AsyncSession, persona: Persona, monday: date,
                           now: datetime) -> list[int]:
    """Chats of the persona with summarized days in the week of `monday`, not rolled up."""
    days = await session.scalars(
        select(ChatDay).join(Chat, Chat.id == ChatDay.chat_id)
        .where(Chat.persona_id == persona.id, ChatDay.day >= monday,
               ChatDay.day <= monday + timedelta(days=6)))
    with_days = {row.chat_id for row in days if usable(row)}
    if not with_days:
        return []
    weeks = await session.scalars(select(ChatWeek).where(ChatWeek.chat_id.in_(with_days),
                                                         ChatWeek.week == monday))
    finished = {row.chat_id for row in weeks if done(row, now)}
    return sorted(with_days - finished)


def night_days(today: date) -> Sequence[date]:
    """The finished days the night job looks at, oldest first."""
    return [today - timedelta(days=n) for n in range(LOOKBACK_DAYS, 0, -1)]


class NightMemory:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps
        self.chronicler = Chronicler(deps)
        self.passports = PassportService(deps)

    async def due(self) -> list[tuple[int, date]]:
        """(persona, day) pairs with work for the night job now."""
        now = self.deps.clock.now()
        found: list[tuple[int, date]] = []
        async with transaction(self.deps.sessions) as session:
            personas = await session.scalars(
                select(Persona).where(Persona.is_active.is_(True)).order_by(Persona.id))
            for persona in personas:
                if not agent_config(persona, AgentKey.MEMORY).model:
                    continue
                for day in night_days(local_day(now, persona.timezone)):
                    if await self._has_work(session, persona, day, now):
                        found.append((persona.id, day))
        return found

    async def _has_work(self, session: AsyncSession, persona: Persona, day: date,
                        now: datetime) -> bool:
        if await chats_to_summarize(session, persona, day, now):
            return True
        return day.weekday() == SUNDAY and bool(
            await chats_to_roll_up(session, persona, week_monday(day), now))

    async def run(self, persona_id: int, day: date) -> NightResult:
        """Summarize `day` of every chat that needs it, update their passports, and on a
        Sunday roll the week up. A day not finished yet for her is left alone."""
        result = NightResult()
        now = self.deps.clock.now()
        async with transaction(self.deps.sessions) as session:
            persona = await session.get(Persona, persona_id)
            if persona is None or day >= local_day(now, persona.timezone):
                return result
            chats = await chats_to_summarize(session, persona, day, now)
        for chat_id in chats:
            outcome = await self.chronicler.day(chat_id, day)
            result.days[chat_id] = outcome
            if outcome == Outcome.WRITTEN and await self._passport(chat_id, day):
                result.passports.append(chat_id)
        if day.weekday() == SUNDAY:
            monday = week_monday(day)
            async with transaction(self.deps.sessions) as session:
                weeks = await chats_to_roll_up(session, persona, monday, now)
            for chat_id in weeks:
                result.weeks[chat_id] = await self.chronicler.week(chat_id, monday)
        return result

    async def rebuild(self, chat_id: int, day: date, *, force: bool = False) -> Outcome:
        """One chat's day, then its passport (the owner's «Пересобрать день»)."""
        outcome = await self.chronicler.day(chat_id, day, force=force)
        if outcome == Outcome.WRITTEN:
            await self._passport(chat_id, day)
        return outcome

    async def _passport(self, chat_id: int, day: date) -> bool:
        """The passport after the day's summary; a Broker failure is logged and the night
        goes on (the next night updates it from its own day)."""
        try:
            return await self.passports.update(chat_id, day)
        except PortError as exc:
            log.warning("chat %s day %s: passport not updated: %s", chat_id, day, exc)
            return False
