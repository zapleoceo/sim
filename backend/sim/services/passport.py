"""The passport writer, «паспортистка» (docs/leads.md), task `agent.update_passport(chat_id)`.

After each persona turn (coalesced: ~5 s, one waiting job per chat) and after every call, the
writer reads the current passport, the chat summary, the newest timeline lines and the facts the
reply plans marked to `remember`, and returns the WHOLE updated passport. The code merges it:
fields the operator edited (`locked_fields`) are never overwritten, an empty text never replaces
a passport, and an edit made while the model was thinking wins (the update is dropped).

The client's words are untrusted data: they travel only inside the JSON user message, never in
the writer's system prompt, and the persona reads the passport marked as data."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select

from sim.db.models import Client, ReplyRun
from sim.db.session import transaction
from sim.domain.agent_prompts import render_prompt
from sim.domain.agents import AgentConfig, AgentKey, agent_config
from sim.domain.enums import LlmPurpose, RunStatus
from sim.domain.passport import Passport, PassportUpdate, merge_update, passport_of, stored
from sim.ports import ChatMessage, ChatPart
from sim.services.context.builder import ChatNotFound, ContextBuilder
from sim.services.deps import Deps
from sim.services.llm_log import describe_messages, logged_call
from sim.services.plan_check import extract_json

log = logging.getLogger(__name__)

PASSPORT_DELAY_S = 5.0          # after a turn: a burst of turns makes one update
DIALOG_LINES = 80               # newest timeline lines the writer reads
REMEMBER_RUNS = 10              # newest sent turns whose `remember` facts are passed on
PASSPORT_SCHEMA = PassportUpdate.model_json_schema()


@dataclass(frozen=True)
class _Snapshot:
    client_id: int
    persona_name: str
    agent: AgentConfig
    passport: Passport
    summary: str
    dialog: list[str]
    remember: list[str] = field(default_factory=list)


class PassportService:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps
        self.builder = ContextBuilder(deps.clock, deps.settings)

    async def update(self, chat_id: int) -> bool:
        """Rewrite the client's passport from this chat. False when there is nothing to do or
        the answer is unusable (the old passport stays)."""
        snap = await self._read(chat_id)
        if snap is None:
            return False
        messages = passport_messages(snap)
        agent = snap.agent
        result = await logged_call(
            self.deps.sessions, purpose=LlmPurpose.PASSPORT, model=agent.model,
            request={"messages": describe_messages(messages), "json_schema": "PassportUpdate"},
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

    async def _read(self, chat_id: int) -> _Snapshot | None:
        async with transaction(self.deps.sessions) as session:
            try:
                know = await self.builder.knowledge(session, chat_id)
            except ChatNotFound:
                return None
            persona = know.persona
            agent = agent_config(persona, AgentKey.PASSPORT)
            if not agent.model or not know.all_lines:
                return None
            runs = await session.scalars(
                select(ReplyRun.plan).where(ReplyRun.chat_id == chat_id,
                                            ReplyRun.status == RunStatus.SENT)
                .order_by(ReplyRun.id.desc()).limit(REMEMBER_RUNS))
            return _Snapshot(
                client_id=know.client.id, persona_name=persona.name, agent=agent,
                passport=passport_of(know.client.passport), summary=know.summary,
                dialog=[line.text for line in know.all_lines[-DIALOG_LINES:]],
                remember=remembered(list(runs)),
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


def remembered(plans: list[dict[str, Any]]) -> list[str]:
    """`remember` facts of the newest turns, oldest first, without repeats."""
    facts: list[str] = []
    for plan in reversed(plans):
        for item in (plan or {}).get("remember") or []:
            fact = " ".join(str((item or {}).get("fact", "")).split())
            if fact and fact not in facts:
                facts.append(fact)
    return facts


def passport_messages(snap: _Snapshot) -> list[ChatMessage]:
    current = snap.passport.model_dump(mode="json", exclude={"updated_at", "locked_fields"})
    payload = {"persona": snap.persona_name, "passport": current, "summary": snap.summary,
               "dialog": snap.dialog, "remember": snap.remember}
    system = render_prompt(snap.agent.prompt, name=snap.persona_name)
    return [
        ChatMessage(role="system", parts=[ChatPart(text=system)]),
        ChatMessage(role="user", parts=[ChatPart(text=json.dumps(payload, ensure_ascii=False))]),
    ]
