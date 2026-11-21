"""The passport writer, «паспортистка» (docs/leads.md, docs/memory.md), once a day at night.

The night job (sim/services/memory/night.py) calls it for every chat whose day the chronicler
has just summarized; the owner's «Обновить паспорт сейчас» runs it for today (task
`agent.update_passport`). The writer reads the current passport, the summaries of the last
days (the night's own day last), that day's verbatim timeline and its «заметки дня», and
returns the WHOLE updated passport. The code merges it: fields the operator edited
(`locked_fields`) are never overwritten, an empty text never replaces a passport, and an edit
made while the model was thinking wins (the update is dropped).

The client's words are untrusted data: they travel only inside the JSON user message, never in
the writer's system prompt, and the persona reads the passport marked as data."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from pydantic import ValidationError

from sim.db.models import Client
from sim.db.session import transaction
from sim.domain.agent_prompts import render_prompt
from sim.domain.agents import AgentConfig, AgentKey, agent_config
from sim.domain.enums import LlmPurpose
from sim.domain.memory import notes_of
from sim.domain.passport import Passport, PassportUpdate, merge_update, passport_of, stored
from sim.ports import ChatMessage, ChatPart
from sim.services.context.builder import ChatNotFound, ContextBuilder, timeline_lines
from sim.services.context.diary import RECENT_DAYS, day_rows, day_title, usable
from sim.services.context.window import day_bounds, local_day
from sim.services.deps import Deps
from sim.services.llm_log import describe_messages, logged_call
from sim.services.plan_check import extract_json

log = logging.getLogger(__name__)

DIALOG_LINES = 200              # newest lines of the day the writer reads
PASSPORT_SCHEMA = PassportUpdate.model_json_schema()


@dataclass(frozen=True)
class _Snapshot:
    client_id: int
    persona_name: str
    agent: AgentConfig
    passport: Passport
    day: date
    days: list[dict[str, object]]
    dialog: list[str]
    remember: list[str] = field(default_factory=list)


class PassportService:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps
        self.builder = ContextBuilder(deps.clock, deps.settings)

    async def update(self, chat_id: int, day: date | None = None) -> bool:
        """Rewrite the client's passport from this chat's `day` (her local date; None = today).
        False when there is nothing to do or the answer is unusable (the passport stays)."""
        snap = await self._read(chat_id, day)
        if snap is None:
            return False
        messages = passport_messages(snap)
        agent = snap.agent
        result = await logged_call(
            self.deps.sessions, purpose=LlmPurpose.PASSPORT, model=agent.model,
            request={"messages": describe_messages(messages), "json_schema": "PassportUpdate",
                     "day": snap.day.isoformat()},
            call=lambda: self.deps.llm.chat(model=agent.model, messages=messages,
                                            json_schema=PASSPORT_SCHEMA,
                                            max_tokens=agent.max_tokens, label="passport",
                                            key_tier=agent.key_tier),
            chat_id=chat_id, key_tier=agent.key_tier,
        )
        try:
            update = PassportUpdate.model_validate(json.loads(extract_json(result.text)))
        except (json.JSONDecodeError, ValidationError) as exc:
            log.warning("chat %s: unreadable passport: %s", chat_id, type(exc).__name__)
            return False
        if not update.text.strip():
            log.warning("chat %s: the passport writer returned an empty text", chat_id)
            return False
        return await self._write(snap, update)

    async def _read(self, chat_id: int, day: date | None) -> _Snapshot | None:
        async with transaction(self.deps.sessions) as session:
            try:
                know = await self.builder.knowledge(session, chat_id)
            except ChatNotFound:
                return None
            persona = know.persona
            agent = agent_config(persona, AgentKey.PASSPORT)
            if not agent.model:
                return None
            target = day or local_day(self.deps.clock.now(), persona.timezone)
            start, end = day_bounds(target, persona.timezone)
            lines = await timeline_lines(session, chat_id, know.speakers, since=start,
                                         until=end)
            rows = await day_rows(session, chat_id, target - timedelta(days=RECENT_DAYS),
                                  target)
            days = [{"date": day_title(d), "summary": rows[d].summary,
                     "facts": list(rows[d].facts or [])} for d in sorted(rows) if usable(rows[d])]
            if not lines and not days:
                return None
            return _Snapshot(
                client_id=know.client.id, persona_name=persona.name, agent=agent,
                passport=passport_of(know.client.passport), day=target, days=days,
                dialog=[line.text for line in lines[-DIALOG_LINES:]],
                remember=notes_of(know.chat.day_notes, target).notes,
            )

    async def _write(self, snap: _Snapshot, update: PassportUpdate) -> bool:
        async with transaction(self.deps.sessions) as session:
            client = await session.get(Client, snap.client_id, with_for_update=True)
            if client is None:
                return False
            current = passport_of(client.passport)
            if current.updated_at != snap.passport.updated_at:
                log.info("client %s: passport changed meanwhile, update dropped", client.id)
                return False
            client.passport = stored(merge_update(current, update, self.deps.clock.now()))
            return True


def passport_messages(snap: _Snapshot) -> list[ChatMessage]:
    current = snap.passport.model_dump(mode="json", exclude={"updated_at", "locked_fields"})
    payload = {"persona": snap.persona_name, "passport": current, "date": day_title(snap.day),
               "days": snap.days, "dialog": snap.dialog, "remember": snap.remember}
    system = render_prompt(snap.agent.prompt, name=snap.persona_name)
    return [
        ChatMessage(role="system", parts=[ChatPart(text=system)]),
        ChatMessage(role="user", parts=[ChatPart(text=json.dumps(payload, ensure_ascii=False))]),
    ]
