"""The attention agent («Внимание», docs/agents.md, docs/attention.md): one cheap, fast call per
phone check for the chats the rules could not settle. Its prompt is the owner's (or the
default) system message; the chats go as one JSON user message; the answer must be
`AttentionPlan`. It never raises: an agent that is off, fails or answers nonsense gives no plan
and a reason, and the caller falls back."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.db.models import Persona
from sim.domain.agent_prompts import render_prompt
from sim.domain.agents import AgentKey, agent_config
from sim.domain.attention import AttentionPlan
from sim.domain.enums import LlmPurpose
from sim.ports import ChatMessage, ChatPart, LlmPort
from sim.services.attention.decide import parse_plan
from sim.services.llm_log import describe_messages, logged_call

log = logging.getLogger(__name__)

PLAN_SCHEMA = AttentionPlan.model_json_schema()
NO_MODEL = "«Внимание» выключено"
UNREADABLE = "«Внимание» ответило неразборчиво"


@dataclass(frozen=True)
class AgentAnswer:
    plan: AttentionPlan | None
    error: str = ""                 # why there is no plan (the fallback's reason)


def attention_messages(persona: Persona, payload: dict[str, Any]) -> list[ChatMessage]:
    system = render_prompt(agent_config(persona, AgentKey.ATTENTION).prompt, name=persona.name)
    user = json.dumps(payload, ensure_ascii=False, indent=1)
    return [ChatMessage(role="system", parts=[ChatPart(text=system)]),
            ChatMessage(role="user", parts=[ChatPart(text=user)])]


class AttentionAgent:
    def __init__(self, llm: LlmPort, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.llm = llm
        self.sessions = sessions

    async def decide(self, persona: Persona, payload: dict[str, Any]) -> AgentAnswer:
        cfg = agent_config(persona, AgentKey.ATTENTION)
        if not cfg.model:
            return AgentAnswer(None, NO_MODEL)
        messages = attention_messages(persona, payload)
        try:
            result = await logged_call(
                self.sessions, purpose=LlmPurpose.ATTENTION, model=cfg.model,
                request={"messages": describe_messages(messages),
                         "json_schema": "AttentionPlan"},
                call=lambda: self.llm.chat(model=cfg.model, messages=messages,
                                           json_schema=PLAN_SCHEMA, max_tokens=cfg.max_tokens,
                                           label="attention", key_tier=cfg.key_tier),
                key_tier=cfg.key_tier)
        except Exception as exc:        # a check must never be lost to the agent
            log.warning("persona %s: attention agent failed: %s", persona.id,
                        str(exc) or type(exc).__name__)
            return AgentAnswer(None, f"«Внимание» не ответило: {type(exc).__name__}")
        plan = parse_plan(result.text)
        return AgentAnswer(plan, "" if plan is not None else UNREADABLE)
