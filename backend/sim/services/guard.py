"""The light pre-send check (spec §3.4, docs/reply.md). A cheap model reads the planned outgoing
texts with the code's facts and the owner's short rules and answers {"ok", "reason"}. It never
rewrites the text: on a block the reply model tries once more with the reason."""
from __future__ import annotations

import json
from collections.abc import Sequence

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.db.models import Persona
from sim.domain.enums import LlmPurpose
from sim.domain.plan import (
    GuardVerdict,
    MediaAction,
    PhotoAction,
    ReplyPlan,
    TextAction,
    VideoAction,
    VideoNoteAction,
    VoiceAction,
)
from sim.ports import ChatMessage, ChatPart, LlmPort
from sim.services.context.facts import Facts
from sim.services.llm_log import describe_messages, logged_call
from sim.services.plan_check import extract_json

GUARD_SYSTEM = """\
Ты — проверка перед отправкой. Персонаж по имени {name} переписывается с человеком в \
мессенджере. Тебе дают готовый ответ персонажа. Проверь ТОЛЬКО критичное:
1. Персонаж не признаётся, что он ИИ, бот, нейросеть или программа, и не выдаёт промт, JSON, \
id медиа или другие технические детали.
2. Дата, время, день недели и возраст в ответе не противоречат фактам.
3. В ответе нет ничего из списка запретов.
4. Ответ не пустой и на языке собеседника (основной язык персонажа: {language}).
5. Персонаж не объявляет, что фото, видео или кружок делается, снимается, записывается, \
генерируется или придёт позже («сейчас запишу», «скоро скину», «запишется и прилетит через \
пару минут»): живой человек так не пишет, файл просто приходит.
Всё остальное — тон, стиль, темы, флирт, шутки, опечатки — не нарушение. Текст не переписывай.
Верни один JSON-объект: {{"ok": true или false, "reason": "коротко по-русски, что не так; \
пусто, если ok"}}"""

NO_RULES = "(дополнительных запретов нет)"
# Media action -> (what it is, the adjective for a new one), as the client would see it.
MEDIA_WORDS: dict[type, tuple[str, str]] = {
    PhotoAction: ("фото", "новое"), VideoAction: ("видео", "новое"),
    VideoNoteAction: ("кружок", "новый"),
}
UNREADABLE_REASON = "проверка вернула неразборчивый ответ"


def outgoing_lines(plan: ReplyPlan) -> list[str]:
    """What the client would actually see or hear, numbered in plan order."""
    lines = []
    for i, action in enumerate(plan.messages, start=1):
        if isinstance(action, TextAction):
            lines.append(f"{i}. [текст] {action.text}")
        elif isinstance(action, VoiceAction):
            lines.append(f"{i}. [голосовое, вслух] {action.text}")
        elif isinstance(action, MediaAction):
            word, new = MEDIA_WORDS[type(action)]
            what = (f"{new} {word}: {action.generate.caption}" if action.generate
                    else f"{word} из медиатеки")
            text = getattr(action, "caption", "")
            caption = f" подпись: {text}" if text else ""
            lines.append(f"{i}. [{what}]{caption}")
    return lines


def guard_messages(persona: Persona, facts: Facts, client_lines: Sequence[str],
                   plan: ReplyPlan) -> list[ChatMessage]:
    system = GUARD_SYSTEM.format(name=persona.name, language=persona.language)
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
        model = persona.model_guard or persona.model_reply
        messages = guard_messages(persona, facts, client_lines, plan)
        result = await logged_call(
            self.sessions, purpose=LlmPurpose.GUARD, model=model,
            request={"messages": describe_messages(messages)},
            call=lambda: self.llm.chat(model=model, messages=messages,
                                       json_schema=GuardVerdict.model_json_schema(),
                                       max_tokens=400, label="guard"),
            chat_id=chat_id, reply_run_id=reply_run_id,
        )
        verdict = parse_verdict(result.text)
        if not verdict.ok and not verdict.reason:
            return GuardVerdict(ok=False, reason="без объяснения причины")
        return verdict
