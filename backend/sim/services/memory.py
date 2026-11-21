"""The memory step (docs/context.md §3-4), task `agent.refresh_memory`.

- When the timeline no longer fits the budget, the oldest events (all but the newest
  `SIM_CONTEXT_KEEP_RECENT_EVENTS`) are folded into `chat.summary` and the boundary
  `summary_until_event_id` moves forward. The old summary is part of the new one.
- The same model call reviews `client.memory`: adds facts from the folded events and from calls
  made since the last memory update, merges repeats, drops what is outdated.
- After every call the step runs even if nothing has to be folded.
Code checks the model: a fold needs a non-empty summary; the boundary only moves forward."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select

from sim.db.models import Chat, ChatEvent, Client
from sim.db.session import transaction
from sim.domain.enums import EventKind, LlmPurpose
from sim.events import append_event
from sim.ports import ChatMessage, ChatPart
from sim.services.client_memory import replace_facts
from sim.services.context.budget import compression_boundary
from sim.services.context.builder import ContextBuilder
from sim.services.context.render import TimelineLine
from sim.services.deps import Deps
from sim.services.llm_log import describe_messages, logged_call
from sim.services.plan_check import extract_json

log = logging.getLogger(__name__)


class MemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(default="", max_length=20_000)
    memory: list[str] = Field(default_factory=list, max_length=100)


MEMORY_SYSTEM = """\
Ты ведёшь память персонажа по имени {name} о собеседнике. Пиши по-русски, коротко и по делу. \
Верни один JSON-объект: {{"summary": "...", "memory": ["...", "..."]}}"""

MEMORY_TASK = """\
Задача:
1. summary — если есть блок «События для сводки»: новая сводка переписки = старая сводка + \
эти события. От третьего лица, по порядку, с датами важных событий, без мелочей. Если такого \
блока нет — верни пустую строку.
2. memory — полный обновлённый список устойчивых фактов о собеседнике: имя, город, работа, \
семья, важные даты, о чём договорились, о чём просил не говорить. Добавь новое из событий и \
звонков, объедини повторы, убери устаревшее. Каждый факт — одна короткая фраза."""


@dataclass(frozen=True)
class _Snapshot:
    chat_id: int
    persona_name: str
    model: str
    old_summary: str
    old_until: int | None
    boundary: int | None
    fold: list[TimelineLine]
    calls: list[TimelineLine]
    memory: list[str]
    last_event_id: int | None


class MemoryService:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps
        self.builder = ContextBuilder(deps.clock, deps.settings)

    async def refresh(self, chat_id: int) -> bool:
        """Returns True when the summary or the memory was updated."""
        snap = await self._read(chat_id)
        if snap is None:
            return False
        messages = memory_messages(snap)
        result = await logged_call(
            self.deps.sessions, purpose=LlmPurpose.MEMORY, model=snap.model,
            request={"messages": describe_messages(messages)},
            call=lambda: self.deps.llm.chat(model=snap.model, messages=messages,
                                            json_schema=MemoryUpdate.model_json_schema(),
                                            max_tokens=4096, label="memory"),
            chat_id=chat_id,
        )
        try:
            update = MemoryUpdate.model_validate(json.loads(extract_json(result.text)))
        except (json.JSONDecodeError, ValidationError) as exc:
            log.warning("chat %s: unreadable memory update: %s", chat_id, exc)
            return False
        if snap.fold and not update.summary.strip():
            log.warning("chat %s: memory model returned an empty summary", chat_id)
            return False
        return await self._write(snap, update)

    async def _read(self, chat_id: int) -> _Snapshot | None:
        async with transaction(self.deps.sessions) as session:
            know = (await self.builder.reply_frame(session, chat_id)).knowledge
            model = know.persona.model_memory or know.persona.model_reply
            if not model:
                return None
            boundary = compression_boundary(know.all_lines, know.timeline_budget,
                                            self.deps.settings.context_keep_recent_events)
            last_memory = (await session.execute(
                select(func.max(ChatEvent.id))
                .where(ChatEvent.chat_id == chat_id, ChatEvent.kind == EventKind.MEMORY_UPDATED)
            )).scalar_one_or_none() or 0
            fold = [line for line in know.all_lines
                    if boundary is not None and line.event_id <= boundary]
            calls = [line for line in know.all_lines
                     if line.kind == EventKind.CALL and line.event_id > last_memory]
            if not fold and not calls:
                return None
            return _Snapshot(
                chat_id=chat_id, persona_name=know.persona.name, model=model,
                old_summary=know.summary, old_until=know.chat.summary_until_event_id,
                boundary=boundary if fold else None, fold=fold, calls=calls,
                memory=[str(item.get("fact", "")) for item in know.client.memory or []],
                last_event_id=know.all_lines[-1].event_id if know.all_lines else None,
            )

    async def _write(self, snap: _Snapshot, update: MemoryUpdate) -> bool:
        async with transaction(self.deps.sessions) as session:
            chat = await session.get(Chat, snap.chat_id, with_for_update=True)
            if chat is None or chat.summary_until_event_id != snap.old_until:
                return False        # another memory step got here first
            client = await session.get(Client, chat.client_id)
            if client is None:
                return False
            if snap.boundary is not None:
                if snap.old_until is not None and snap.boundary <= snap.old_until:
                    return False
                chat.summary = update.summary.strip()
                chat.summary_until_event_id = snap.boundary
            old_memory = list(client.memory or [])
            if update.memory or not old_memory:
                client.memory = replace_facts(old_memory, update.memory,
                                              event_id=snap.last_event_id,
                                              at=self.deps.clock.now())
            await append_event(
                session, chat_id=chat.id, kind=EventKind.MEMORY_UPDATED,
                data={"summary_until_event_id": chat.summary_until_event_id,
                      "folded_events": len(snap.fold), "calls": len(snap.calls),
                      "facts": len(client.memory or [])},
            )
            return True


def memory_messages(snap: _Snapshot) -> list[ChatMessage]:
    blocks = [f"# Старая сводка\n{snap.old_summary or '(нет)'}"]
    if snap.fold:
        blocks.append("# События для сводки\n" + "\n".join(line.text for line in snap.fold))
    if snap.calls:
        blocks.append("# Новые звонки\n" + "\n".join(line.text for line in snap.calls))
    facts = "\n".join(f"- {fact}" for fact in snap.memory if fact.strip()) or "(пусто)"
    blocks += [f"# Что персонаж помнит о собеседнике сейчас\n{facts}", MEMORY_TASK]
    return [
        ChatMessage(role="system",
                    parts=[ChatPart(text=MEMORY_SYSTEM.format(name=snap.persona_name))]),
        ChatMessage(role="user", parts=[ChatPart(text="\n\n".join(blocks))]),
    ]
