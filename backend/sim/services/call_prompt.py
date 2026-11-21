"""System prompt of a phone call: pure functions, no IO.

A live voice model gets ONE system instruction (no JSON contract, no separate history turns), so
the call prompt is the persona prompt and strategy (the same stable text a chat reply starts
with, minus the answer format), the call rules, the chat so far as data, and the dynamic dialog
context.

The call keeps the chat persona, with a short greeting and spoken-format rules. It answers
questions about its artificial nature honestly and never invents personal facts.

Every instruction text here is an owner setting (sim.services.instructions, «Общие правила →
3. Звонок»); code fills in the persona name and the chat transcript."""
from __future__ import annotations

from sim.services import instructions
from sim.services.prompt import (
    DialogContext,
    PersonaProfile,
    PromptTexts,
    StrategyProfile,
    Turn,
    dynamic_context,
    merge_turns,
    stable_system_prompt,
)

CALL_HISTORY_CHARS = 18000
CALL_TURN_MAX = 1500


def call_identity(name: str) -> str:
    return instructions.fill("call_identity", name=name)


def greeting(name: str) -> str:
    """The typed first turn requests only «Алло», independently of the persona name."""
    return instructions.fill("call_greeting", name=name)


def chat_transcript(history: list[Turn]) -> str:
    """The shared window as data, newest first for budget selection, chronological for reading.
    Voice has a hard character budget even when the owner's chat window is much larger."""
    lines: list[str] = []
    size = 0
    trimmed = False
    for turn in reversed(merge_turns(history)):
        who = "Клиент" if turn["role"] == "user" else "Ты"
        full = " ".join(turn["content"].split())
        text = full[-CALL_TURN_MAX:]
        trimmed = trimmed or len(text) < len(full)
        line = f"{who}: {text}"
        if size + len(line) + 1 > CALL_HISTORY_CHARS:
            trimmed = True
            break
        lines.append(line)
        size += len(line) + 1
    lines.reverse()
    if trimmed:
        lines.insert(0, instructions.text("call_history_trimmed"))
    return "\n".join(lines)


def call_system_prompt(
    persona: PersonaProfile, strategy: StrategyProfile, history: list[Turn], ctx: DialogContext,
) -> str:
    parts = [
        stable_system_prompt(persona, strategy, PromptTexts(contract="")).rstrip(),
        instructions.text("call_rules"),
        call_identity(persona.name),
    ]
    chat = chat_transcript(history)
    if chat:
        parts.append(instructions.text("call_history_header") + "\n" + chat)
    parts.append(dynamic_context(ctx, strategy))
    return "\n\n".join(parts)
