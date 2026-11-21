"""May the AI answer in this chat? The one rule (docs/reply.md, «Может ли ИИ отвечать в чате»)
for incoming messages, phone checks, reply turns, calls, the team's status and waking chats
when the AI is switched on.

The persona must be the chat's OWN persona (`chat.persona_id`, never another one), active (not
archived), with the AI on for her, for the client and in the chat, and the chat must not wait
for an operator. The first failed condition is the reason (`AiBlock`), in this order; each
caller words it for its own reader (the status line, a call's `error`, a turn's reason).

`ai_block` is pure; `sim/services/ai_gate.py` is the same rule as a WHERE clause."""
from __future__ import annotations

from enum import StrEnum
from typing import Protocol


class AiBlock(StrEnum):
    NO_PERSONA = "no_persona"           # no persona, or not the chat's own persona
    ARCHIVED = "archived"               # persona.is_active is off
    PERSONA_OFF = "persona_off"         # persona.ai_enabled
    CLIENT_OFF = "client_off"           # no client, or client.ai_enabled
    CHAT_OFF = "chat_off"               # chat.ai_enabled
    NEEDS_OPERATOR = "needs_operator"   # chat.needs_operator: silent until «Оператор разобрался»


class GatedPersona(Protocol):
    @property
    def id(self) -> int: ...

    @property
    def is_active(self) -> bool: ...

    @property
    def ai_enabled(self) -> bool: ...


class GatedClient(Protocol):
    @property
    def ai_enabled(self) -> bool: ...


class GatedChat(Protocol):
    @property
    def persona_id(self) -> int: ...

    @property
    def ai_enabled(self) -> bool: ...

    @property
    def needs_operator(self) -> bool: ...


def persona_block(persona: GatedPersona | None) -> AiBlock | None:
    """The persona's own part of the rule (a phone check covers all her chats)."""
    if persona is None:
        return AiBlock.NO_PERSONA
    if not persona.is_active:
        return AiBlock.ARCHIVED
    if not persona.ai_enabled:
        return AiBlock.PERSONA_OFF
    return None


def ai_block(persona: GatedPersona | None, client: GatedClient | None,
             chat: GatedChat) -> AiBlock | None:
    """Why the AI must not answer in `chat`, or None when it may."""
    if persona is None or persona.id != chat.persona_id:
        return AiBlock.NO_PERSONA
    blocked = persona_block(persona)
    if blocked is not None:
        return blocked
    if client is None or not client.ai_enabled:
        return AiBlock.CLIENT_OFF
    if not chat.ai_enabled:
        return AiBlock.CHAT_OFF
    if chat.needs_operator:
        return AiBlock.NEEDS_OPERATOR
    return None
