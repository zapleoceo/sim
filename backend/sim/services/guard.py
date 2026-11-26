"""The light pre-send check (spec §3.4, docs/reply.md). A cheap model reads the planned outgoing
texts with the code's facts and the owner's short rules and answers {"ok", "reason"}. It never
rewrites the text: on a block the reply model tries once more with the reason."""
from __future__ import annotations

import json
from collections.abc import Sequence

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.db.models import Persona
from sim.domain.agent_prompts import render_prompt
from sim.domain.agents import AgentKey, agent_config
from sim.domain.enums import LlmPurpose
from sim.domain.plan import (
    GuardVerdict,
    MediaAction,
    PhotoAction,
    ReplyPlan,
    StickerAction,
    TextAction,
    VideoAction,
    VideoNoteAction,
    VoiceAction,
)
from sim.ports import ChatMessage, ChatPart, LlmPort
from sim.services.context.facts import Facts
from sim.services.llm_log import describe_messages, logged_call
from sim.services.plan_check import extract_json

NO_RULES = "(дополнительных запретов нет)"
# Media action -> how the client sees it: the file itself, not its generation task.
MEDIA_WORDS: dict[type, str] = {PhotoAction: "фото", VideoAction: "видео",
                                VideoNoteAction: "кружок"}
UNREADABLE_REASON = "проверка вернула неразборчивый ответ"


def outgoing_lines(plan: ReplyPlan) -> list[str]:
    """Only what the client will actually see or hear, numbered in plan order. A photo, video or
    video note is the file itself — shown as a bracketed mark, never with its internal
    generation task (scene prompt, library caption, tags): the guard once read «новый кружок:
    приветствие для Max» as the persona announcing a recording and blocked a good answer."""
    lines = []
    for i, action in enumerate(plan.messages, start=1):
        if isinstance(action, TextAction):
            lines.append(f"{i}. [текст] {action.text}")
        elif isinstance(action, VoiceAction):
            lines.append(f"{i}. [голосовое, вслух] {action.text}")
        elif isinstance(action, StickerAction):
            lines.append(f"{i}. [стикер]")
        elif isinstance(action, MediaAction):
            parts = [f"{i}. [{MEDIA_WORDS[type(action)]}]"]
            speech = action.generate.speech if action.generate else ""
            if speech:
                parts.append(f"в кадре говорит вслух: {speech}")
            caption = getattr(action, "caption", "")
            if caption:
                parts.append(f"подпись: {caption}")
            lines.append(" ".join(parts))
    return lines


def guard_messages(persona: Persona, facts: Facts, client_lines: Sequence[str],
                   plan: ReplyPlan) -> list[ChatMessage]:
    """The guard's prompt (the owner's or the default, docs/agents.md) as the system message;
    facts, the rule list and the planned answer as the user message."""
    system = render_prompt(agent_config(persona, AgentKey.GUARD).prompt, name=persona.name,
                           language=persona.language)
    user = "\n\n".join([
        f"# Факты\n{facts.text}",
        f"# Запреты\n{persona.guard_rules.strip() or NO_RULES}",
        "# Последние сообщения собеседника\n" + ("\n".join(client_lines) or "(нет)"),
        "# Ответ персонажа\n" + "\n".join(outgoing_lines(plan)),
    ])
    return [ChatMessage(role="system", parts=[ChatPart(text=system)]),
            ChatMessage(role="user", parts=[ChatPart(text=user)])]


def parse_verdict(text: str) -> GuardVerdict:
    try:
        return GuardVerdict.model_validate(json.loads(extract_json(text)))
    except (json.JSONDecodeError, ValidationError):
        return GuardVerdict(ok=False, reason=UNREADABLE_REASON)


class Guard:
    def __init__(self, llm: LlmPort, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.llm = llm
        self.sessions = sessions

    async def check(self, *, persona: Persona, facts: Facts, client_lines: Sequence[str],
                    plan: ReplyPlan, chat_id: int, reply_run_id: int) -> GuardVerdict:
        """Fails closed: an unreadable answer is a block; a Broker error propagates."""
        cfg = agent_config(persona, AgentKey.GUARD)
        messages = guard_messages(persona, facts, client_lines, plan)
        result = await logged_call(
            self.sessions, purpose=LlmPurpose.GUARD, model=cfg.model,
            request={"messages": describe_messages(messages)},
            call=lambda: self.llm.chat(model=cfg.model, messages=messages,
                                       json_schema=GuardVerdict.model_json_schema(),
                                       max_tokens=cfg.max_tokens, label="guard",
                                       key_tier=cfg.key_tier),
            chat_id=chat_id, reply_run_id=reply_run_id, key_tier=cfg.key_tier,
        )
        verdict = parse_verdict(result.text)
        if not verdict.ok and not verdict.reason:
            return GuardVerdict(ok=False, reason="без объяснения причины")
        return verdict
