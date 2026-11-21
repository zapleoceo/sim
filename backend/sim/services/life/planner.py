"""The daily planner (docs/persona-life.md, «План дня»): the model `persona.model_life` writes one
day of her life — 6–14 events from waking up to bedtime, consistent with her routine, with small
concrete incidents, an outfit and a sound tag per event — moves her story arcs a little and
sums up the last finished day.

- Task `agent.life_tick` (every 30 min) asks `due_days`: TODAY when it is missing, TOMORROW from
  03:00 local time. Task `agent.plan_day` runs `LifePlanner.plan` for one day.
- A day is planned once (UNIQUE persona + day). It is never re-planned by itself; only the
  owner's «Спланировать заново» (`force`) replaces it. An owner-edited day is kept.
- A failed plan is stored as a row with `error` and no events (the context falls back to the
  routine); the tick tries again after `RETRY_AFTER`.
- Code checks the answer: schema, times in order without overlaps, outfits that change only
  with a reason. One repair round with the list of errors, like the reply plan."""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Persona, PersonaArc, PersonaCanon, PersonaDay
from sim.db.session import transaction
from sim.domain.enums import ArcStatus, CanonStatus, DaySource, LlmPurpose
from sim.domain.life import Ambience, DayEvent, DayPlan, day_errors, outfit_errors
from sim.ports import ChatMessage, ChatPart
from sim.services.context.facts import WEEKDAYS, human_date, to_local
from sim.services.context.render import one_line
from sim.services.deps import Deps
from sim.services.life.calendar import date_facts
from sim.services.life.render import routine_block
from sim.services.life.store import load_days
from sim.services.life.world import day_events, life_of
from sim.services.llm_log import describe_messages, logged_call
from sim.services.plan_check import extract_json

log = logging.getLogger(__name__)

PLAN_TOMORROW_FROM_HOUR = 3
RETRY_AFTER = timedelta(hours=3)
HISTORY_DAYS = 3
PROMPT_CHARS = 6000
CANON_FOR_PLANNER = 20
PLANNER_MAX_TOKENS = 6000
PLAN_SCHEMA = DayPlan.model_json_schema()

PLANNER_SYSTEM = """\
Ты придумываешь один день из жизни персонажа по имени {name}: что она делает с утра до \
вечера. Пиши по-русски. Верни один JSON-объект строго по схеме, без текста вокруг: \
{{"events": [...], "arc_updates": [...], "yesterday_summary": "..."}}"""

PLANNER_TASK = """\
# Задача
- events — 6–14 событий по порядку от пробуждения до сна, без наложений, время "ЧЧ:ММ" по её \
часам. Опирайся на распорядок этого дня недели, людей, увлечения, праздники и её истории; \
сон ночью не расписывай.
- В каждом событии: title (что делает), place (где), with_whom (имена из её мира), busy \
(free — свободна, partial — занята, away — телефон не в руках), details — 1–2 мелкие конкретные \
подробности (что сломалось, что сказал Андрей, что купила, какая погода).
- outfit — во что она одета в это событие, конкретно (цвет, вещь). Одежда держится весь день и \
меняется только по понятной причине: после пробежки и душа, когда выходит из дома или \
возвращается, перед сном. Если место то же и паузы нет — одежда та же, слово в слово.
- ambience — звук места: {ambience}.
- arc_updates — истории движутся медленно: меняй stage одной-двух историй, только если в этот \
день с ними что-то происходит; status done — когда история закончилась. arc_id только из списка.
- yesterday_summary — {summary_rule}
- Не повторяй дословно прошлые дни: жизнь та же, но мелочи каждый день свои."""

REPAIR_NOTE = ("Ответ не подошёл: {errors}. Верни исправленный JSON-объект строго по схеме, "
               "без текста вокруг.")


class Outcome(StrEnum):
    PLANNED = "planned"
    EXISTS = "exists"           # planned already (or edited by the owner)
    FAILED = "failed"
    SKIPPED = "skipped"         # no persona or no planner model


class PlanRejected(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def needs_plan(row: PersonaDay | None, now: datetime) -> bool:
    """A missing day, or a failed one (no events, not edited) after `RETRY_AFTER`."""
    if row is None:
        return True
    if row.events or row.source == DaySource.EDITED:
        return False
    return bool(row.error) and now - row.updated_at >= RETRY_AFTER


def days_to_plan(now: datetime, tz: str, rows: Mapping[date, PersonaDay]) -> list[date]:
    """Today if it has no plan; tomorrow from `PLAN_TOMORROW_FROM_HOUR` local time."""
    local = to_local(now, tz)
    today = local.date()
    wanted = [today]
    if local.hour >= PLAN_TOMORROW_FROM_HOUR:
        wanted.append(today + timedelta(days=1))
    return [day for day in wanted if needs_plan(rows.get(day), now)]


def parse_day_plan(text: str) -> DayPlan:
    try:
        plan = DayPlan.model_validate(json.loads(extract_json(text)))
    except json.JSONDecodeError as exc:
        raise PlanRejected([f"это не JSON: {exc.msg}"]) from exc
    except ValidationError as exc:
        raise PlanRejected([f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                            for e in exc.errors()]) from exc
    if errors := day_errors(plan.events) + outfit_errors(plan.events):
        raise PlanRejected(errors)
    return plan


async def locked_day(session: AsyncSession, persona_id: int, day: date) -> PersonaDay | None:
    return await session.scalar(
        select(PersonaDay).where(PersonaDay.persona_id == persona_id, PersonaDay.day == day)
        .with_for_update())


@dataclass(frozen=True)
class _Input:
    persona_id: int
    model: str
    messages: list[ChatMessage]
    arc_ids: frozenset[int]
    summary_day: date | None


def _events_lines(events: list[DayEvent]) -> list[str]:
    return [f"- {e.start}–{e.end} {one_line(e.title)}"
            + (f" ({one_line(e.place)})" if e.place.strip() else "")
            + (f": {one_line(e.details)}" if e.details.strip() else "") for e in events]


def planner_messages(persona: Persona, day: date, *, history: Mapping[date, PersonaDay],
                     arcs: list[PersonaArc], canon: list[str],
                     summary_day: date | None) -> list[ChatMessage]:
    life = life_of(persona)
    blocks = []
    if persona.prompt.strip():
        blocks.append(f"# Кто она\n{persona.prompt.strip()[:PROMPT_CHARS]}")
    blocks.append("# День, который нужно спланировать\n" + " ".join(date_facts(day)))
    blocks.append(routine_block(life))
    if arcs:
        blocks.append("# Её истории сейчас (arc_id: название — суть. стадия)\n" + "\n".join(
            f"- {arc.id}: {arc.title} — {one_line(arc.summary)} Сейчас: {one_line(arc.stage)}"
            for arc in arcs))
    past = []
    for offset in range(HISTORY_DAYS, 0, -1):
        previous = day - timedelta(days=offset)
        row = history.get(previous)
        if row is None:
            continue
        title = f"## {WEEKDAYS[previous.weekday()]}, {human_date(previous)}"
        text = [title]
        if row.summary.strip():
            text.append(f"Как прошёл: {one_line(row.summary)}")
        text += _events_lines(day_events(row.events))
        past.append("\n".join(text))
    if past:
        blocks.append("# Последние дни\n" + "\n\n".join(past))
    if canon:
        blocks.append("# Что она уже рассказывала о себе (не противоречь)\n"
                      + "\n".join(f"- {one_line(fact)}" for fact in canon))
    rule = "пустая строка."
    if summary_day is not None:
        rule = (f"2–3 предложения от третьего лица о том, как прошёл {human_date(summary_day)} "
                "(день в блоке «Последние дни»), по его событиям и подробностям.")
    ambience = ", ".join(a.value for a in Ambience)
    blocks.append(PLANNER_TASK.format(ambience=ambience, summary_rule=rule))
    system = PLANNER_SYSTEM.format(name=persona.name)
    return [ChatMessage(role="system", parts=[ChatPart(text=system)]),
            ChatMessage(role="user", parts=[ChatPart(text="\n\n".join(blocks))])]


class LifePlanner:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    async def plan(self, persona_id: int, day: date, *, force: bool = False) -> Outcome:
        prepared = await self._read(persona_id, day, force=force)
        if isinstance(prepared, Outcome):
            return prepared
        try:
            plan = await self._ask(prepared)
        except PlanRejected as exc:
            log.warning("persona %s day %s: plan rejected: %s", persona_id, day, exc)
            await self._fail(prepared, day, f"план не прошёл проверку: {exc}"[:2000])
            return Outcome.FAILED
        try:
            return await self._write(prepared, day, plan, force=force)
        except IntegrityError:
            return Outcome.EXISTS      # planned in parallel by another task

    async def _read(self, persona_id: int, day: date, *, force: bool) -> _Input | Outcome:
        async with transaction(self.deps.sessions) as session:
            persona = await session.get(Persona, persona_id)
            if persona is None or not persona.model_life:
                return Outcome.SKIPPED
            history = await load_days(session, persona_id, day - timedelta(days=HISTORY_DAYS),
                                      day)
            current = history.pop(day, None)
            if current is not None and not force and (current.events
                                                      or current.source == DaySource.EDITED):
                return Outcome.EXISTS
            arcs = list(await session.scalars(
                select(PersonaArc).where(PersonaArc.persona_id == persona_id,
                                         PersonaArc.status == ArcStatus.ACTIVE)
                .order_by(PersonaArc.id)))
            canon = list(await session.scalars(
                select(PersonaCanon.fact).where(PersonaCanon.persona_id == persona_id,
                                                PersonaCanon.status == CanonStatus.ACTIVE)
                .order_by(PersonaCanon.id.desc()).limit(CANON_FOR_PLANNER)))
            last_done = to_local(self.deps.clock.now(), persona.timezone).date() - timedelta(
                days=1)
            target = history.get(last_done)
            summary_day = (last_done if target is not None and target.events
                           and not target.summary.strip() else None)
            return _Input(
                persona_id=persona_id, model=persona.model_life,
                messages=planner_messages(persona, day, history=history, arcs=arcs,
                                          canon=list(reversed(canon)), summary_day=summary_day),
                arc_ids=frozenset(arc.id for arc in arcs), summary_day=summary_day)

    async def _ask(self, prepared: _Input) -> DayPlan:
        raw = await self._call(prepared, prepared.messages)
        try:
            return parse_day_plan(raw)
        except PlanRejected as exc:
            repair = [*prepared.messages,
                      ChatMessage(role="assistant", parts=[ChatPart(text=raw)]),
                      ChatMessage(role="user", parts=[ChatPart(
                          text=REPAIR_NOTE.format(errors="; ".join(exc.errors)))])]
            return parse_day_plan(await self._call(prepared, repair))

    async def _call(self, prepared: _Input, messages: list[ChatMessage]) -> str:
        result = await logged_call(
            self.deps.sessions, purpose=LlmPurpose.LIFE, model=prepared.model,
            request={"messages": describe_messages(messages), "json_schema": "DayPlan"},
            call=lambda: self.deps.llm.chat(model=prepared.model, messages=messages,
                                            json_schema=PLAN_SCHEMA,
                                            max_tokens=PLANNER_MAX_TOKENS, label="life"),
        )
        return result.text

    async def _write(self, prepared: _Input, day: date, plan: DayPlan, *,
                     force: bool) -> Outcome:
        async with transaction(self.deps.sessions) as session:
            row = await locked_day(session, prepared.persona_id, day)
            if row is not None and not force and (row.events or row.source == DaySource.EDITED):
                return Outcome.EXISTS
            if row is None:
                row = PersonaDay(persona_id=prepared.persona_id, day=day, summary="")
                session.add(row)
            row.events = [event.model_dump(mode="json") for event in plan.events]
            row.source = DaySource.MODEL
            row.model = prepared.model
            row.error = None
            for update in plan.arc_updates:
                arc = await session.get(PersonaArc, update.arc_id)
                if arc is None or update.arc_id not in prepared.arc_ids:
                    continue
                arc.stage, arc.status = update.stage.strip(), update.status
            if prepared.summary_day is not None and plan.yesterday_summary.strip():
                previous = await locked_day(session, prepared.persona_id, prepared.summary_day)
                if previous is not None and not previous.summary.strip():
                    previous.summary = plan.yesterday_summary.strip()
            await session.flush()
        return Outcome.PLANNED

    async def _fail(self, prepared: _Input, day: date, error: str) -> None:
        """Keeps an existing plan; a missing day gets an error row (no events)."""
        try:
            async with transaction(self.deps.sessions) as session:
                row = await locked_day(session, prepared.persona_id, day)
                if row is None:
                    row = PersonaDay(persona_id=prepared.persona_id, day=day, summary="",
                                     events=[], source=DaySource.MODEL, model=prepared.model)
                    session.add(row)
                row.error = error
        except IntegrityError:
            log.info("persona %s day %s: planned in parallel", prepared.persona_id, day)

    async def due(self) -> list[tuple[int, date]]:
        """(persona, day) pairs the tick should plan now."""
        now = self.deps.clock.now()
        found: list[tuple[int, date]] = []
        async with transaction(self.deps.sessions) as session:
            personas = list(await session.scalars(
                select(Persona).where(Persona.model_life != "", Persona.is_active.is_(True))
                .order_by(Persona.id)))
            for persona in personas:
                today = to_local(now, persona.timezone).date()
                rows = await load_days(session, persona.id, today, today + timedelta(days=1))
                found += [(persona.id, day)
                          for day in days_to_plan(now, persona.timezone, rows)]
        return found
