"""The chronicler, «Летописец» (agent `memory`, docs/agents.md, docs/memory.md): writes the
detailed summary of one persona-local day of a chat (`chat_day`), the rollup of a week
(`chat_week`) and, when a day does not fit the context budget, today's compressed morning.

One prompt (editable in «Агенты») covers the three tasks; the user message is one JSON with
`task` = day | week | morning and the material — the client's words travel only inside it, as
data. The answer is `Digest` {summary, facts}. A day or week the owner edited (`source =
edited`) is never overwritten by the night job; «Пересобрать день» (`force`) rewrites it."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, ChatDay, ChatWeek, Client, Persona
from sim.db.session import transaction
from sim.domain.agent_prompts import render_prompt
from sim.domain.agents import AgentConfig, AgentKey, agent_config
from sim.domain.enums import DaySource, EventKind, LlmPurpose
from sim.domain.memory import DiaryTask, Digest, clean_facts, notes_of
from sim.domain.passport import passport_of
from sim.events import append_event
from sim.ports import ChatMessage, ChatPart, PortError
from sim.services.context.budget import fit_newest
from sim.services.context.builder import speakers_of, timeline_lines
from sim.services.context.diary import day_rows, day_title, usable, week_title
from sim.services.context.window import day_bounds
from sim.services.deps import Deps
from sim.services.llm_log import describe_messages, logged_call
from sim.services.plan_check import extract_json

log = logging.getLogger(__name__)

DAY_DIALOG_TOKENS = 40_000      # one day's timeline for the night summary (newest kept)
PREVIOUS_DAYS = 3               # earlier day summaries given for continuity
DIGEST_SCHEMA = Digest.model_json_schema()


class Outcome(StrEnum):
    WRITTEN = "written"
    EXISTS = "exists"           # already summarized, or edited by the owner
    EMPTY = "empty"             # nothing happened in that period
    SKIPPED = "skipped"         # no chat, or the chronicler has no model
    FAILED = "failed"           # the answer was unusable: an error row is kept


class DigestRejected(Exception):
    pass


@dataclass(frozen=True)
class Writer:
    """Who writes and for whom: the chat's persona and her chronicler settings."""

    chat_id: int
    persona_name: str
    agent: AgentConfig


def writer_of(persona: Persona, chat_id: int) -> Writer | None:
    agent = agent_config(persona, AgentKey.MEMORY)
    return Writer(chat_id, persona.name, agent) if agent.model else None


def chronicler_messages(writer: Writer, payload: dict[str, Any]) -> list[ChatMessage]:
    system = render_prompt(writer.agent.prompt, name=writer.persona_name)
    return [ChatMessage(role="system", parts=[ChatPart(text=system)]),
            ChatMessage(role="user", parts=[ChatPart(
                text=json.dumps(payload, ensure_ascii=False))])]


async def ask_chronicler(deps: Deps, writer: Writer, payload: dict[str, Any]) -> Digest:
    """One logged call (`llm_call.purpose = memory`); raises DigestRejected on an unusable
    answer and PortError when the Broker fails."""
    messages = chronicler_messages(writer, payload)
    agent = writer.agent
    result = await logged_call(
        deps.sessions, purpose=LlmPurpose.MEMORY, model=agent.model,
        request={"messages": describe_messages(messages), "json_schema": "Digest",
                 "task": payload.get("task")},
        call=lambda: deps.llm.chat(model=agent.model, messages=messages,
                                   json_schema=DIGEST_SCHEMA, max_tokens=agent.max_tokens,
                                   label="memory", key_tier=agent.key_tier),
        chat_id=writer.chat_id, key_tier=agent.key_tier,
    )
    try:
        digest = Digest.model_validate(json.loads(extract_json(result.text)))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise DigestRejected(f"нечитаемый ответ: {type(exc).__name__}") from exc
    if not digest.summary.strip():
        raise DigestRejected("пустой итог")
    return Digest(summary=digest.summary.strip(), facts=clean_facts(digest.facts))


@dataclass(frozen=True)
class _Material:
    writer: Writer
    payload: dict[str, Any]
    first_event_id: int | None
    last_event_id: int | None


class Chronicler:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    # --- one day --------------------------------------------------------------------------

    async def day(self, chat_id: int, day: date, *, force: bool = False) -> Outcome:
        material = await self._day_material(chat_id, day, force=force)
        if isinstance(material, Outcome):
            return material
        return await self._summarize(ChatDay, "day", day, material, force=force)

    async def _day_material(self, chat_id: int, day: date, *,
                            force: bool) -> _Material | Outcome:
        async with transaction(self.deps.sessions) as session:
            loaded = await _chat(session, chat_id)
            if loaded is None:
                return Outcome.SKIPPED
            chat, persona, client = loaded
            writer = writer_of(persona, chat_id)
            if writer is None:
                return Outcome.SKIPPED
            row = await _row(session, ChatDay, "day", chat_id, day)
            if not force and _settled(row):
                return Outcome.EXISTS
            start, end = day_bounds(day, persona.timezone)
            lines = await timeline_lines(session, chat_id, speakers_of(persona, client),
                                         since=start, until=end)
            if not lines:
                return Outcome.EMPTY
            kept = fit_newest(lines, DAY_DIALOG_TOKENS)
            cut = [] if len(kept) == len(lines) else ["(начало дня не поместилось)"]
            earlier = await day_rows(session, chat_id, day - timedelta(days=PREVIOUS_DAYS),
                                     day - timedelta(days=1))
            payload = {
                "task": DiaryTask.DAY.value, "persona": persona.name,
                "client": speakers_of(persona, client).client, "date": day_title(day),
                "passport": passport_of(client.passport).text,
                "previous_days": [{"date": day_title(d), "summary": earlier[d].summary}
                                  for d in sorted(earlier) if usable(earlier[d])],
                "notes": notes_of(chat.day_notes, day).notes,
                "dialog": [*cut, *(line.text for line in kept)],
            }
            return _Material(writer, payload, lines[0].event_id, lines[-1].event_id)

    # --- one week -------------------------------------------------------------------------

    async def week(self, chat_id: int, monday: date, *, force: bool = False) -> Outcome:
        material = await self._week_material(chat_id, monday, force=force)
        if isinstance(material, Outcome):
            return material
        return await self._summarize(ChatWeek, "week", monday, material, force=force)

    async def _week_material(self, chat_id: int, monday: date, *,
                             force: bool) -> _Material | Outcome:
        async with transaction(self.deps.sessions) as session:
            loaded = await _chat(session, chat_id)
            if loaded is None:
                return Outcome.SKIPPED
            _, persona, client = loaded
            writer = writer_of(persona, chat_id)
            if writer is None:
                return Outcome.SKIPPED
            if not force and _settled(await _row(session, ChatWeek, "week", chat_id, monday)):
                return Outcome.EXISTS
            rows = await day_rows(session, chat_id, monday, monday + timedelta(days=6))
            days = [rows[d] for d in sorted(rows) if usable(rows[d])]
            if not days:
                return Outcome.EMPTY
            payload = {
                "task": DiaryTask.WEEK.value, "persona": persona.name,
                "client": speakers_of(persona, client).client, "week": week_title(monday),
                "days": [{"date": day_title(row.day), "summary": row.summary,
                          "facts": list(row.facts or [])} for row in days],
            }
            firsts = [row.first_event_id for row in days if row.first_event_id is not None]
            lasts = [row.last_event_id for row in days if row.last_event_id is not None]
            return _Material(writer, payload, min(firsts, default=None),
                             max(lasts, default=None))

    # --- shared ---------------------------------------------------------------------------

    async def _summarize(self, model: type[ChatDay] | type[ChatWeek], column: str,
                         period: date, material: _Material, *, force: bool) -> Outcome:
        try:
            digest = await ask_chronicler(self.deps, material.writer, material.payload)
        except DigestRejected as exc:
            await self._fail(model, column, period, material, str(exc))
            return Outcome.FAILED
        except PortError as exc:
            if exc.retryable:
                raise
            await self._fail(model, column, period, material, str(exc) or "ошибка Broker")
            return Outcome.FAILED
        try:
            async with transaction(self.deps.sessions) as session:
                row = await _row(session, model, column, material.writer.chat_id, period,
                                 lock=True)
                if row is not None and row.source == DaySource.EDITED and not force:
                    return Outcome.EXISTS           # the owner edited it meanwhile
                if row is None:
                    row = model(chat_id=material.writer.chat_id, **{column: period})
                    session.add(row)
                row.summary = digest.summary
                row.facts = digest.facts
                row.first_event_id = material.first_event_id
                row.last_event_id = material.last_event_id
                row.source = DaySource.MODEL
                row.model = material.writer.agent.model
                row.error = None
                await append_event(session, chat_id=material.writer.chat_id,
                                   kind=EventKind.MEMORY_UPDATED,
                                   data={"kind": column, column: period.isoformat()})
        except IntegrityError:
            return Outcome.EXISTS                   # written in parallel
        return Outcome.WRITTEN

    async def _fail(self, model: type[ChatDay] | type[ChatWeek], column: str, period: date,
                    material: _Material, error: str) -> None:
        """Keeps an existing summary; a missing one gets an error row (retried later)."""
        log.warning("chat %s %s %s: %s", material.writer.chat_id, column, period, error)
        try:
            async with transaction(self.deps.sessions) as session:
                row = await _row(session, model, column, material.writer.chat_id, period,
                                 lock=True)
                if row is None:
                    row = model(chat_id=material.writer.chat_id, summary="", facts=[],
                                source=DaySource.MODEL, model=material.writer.agent.model,
                                **{column: period})
                    session.add(row)
                row.error = error[:2000]
        except IntegrityError:
            log.info("chat %s %s %s: written in parallel", material.writer.chat_id, column,
                     period)


def _settled(row: ChatDay | ChatWeek | None) -> bool:
    """Nothing for the night job to do: summarized, or edited by the owner."""
    return row is not None and (row.source == DaySource.EDITED or usable(row))


async def _chat(session: AsyncSession, chat_id: int) -> tuple[Chat, Persona, Client] | None:
    chat = await session.get(Chat, chat_id)
    if chat is None:
        return None
    persona = await session.get(Persona, chat.persona_id)
    client = await session.get(Client, chat.client_id)
    if persona is None or client is None:
        return None
    return chat, persona, client


async def _row[R: (ChatDay, ChatWeek)](session: AsyncSession, model: type[R], column: str,
                                       chat_id: int, period: date, *,
                                       lock: bool = False) -> R | None:
    query = select(model).where(model.chat_id == chat_id,
                                getattr(model, column) == period)
    if lock:
        query = query.with_for_update()
    return await session.scalar(query)
