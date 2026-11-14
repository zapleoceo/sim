"""The map of a persona's prompt for the admin: every piece the model gets, where it comes from
and where the owner edits it. Pure functions, no IO.

- `parts`: the stable system prompt piece by piece (`sim.services.prompt.system_prompt_parts`);
- `context`: an example of the dynamic message code builds for every reply, with her real facts
  for this moment (age from the birth date, local time, where she is by her routine) and
  placeholder client data;
- `rules`: what is fixed in the code and not editable: the phone call rule, the AI video note
  and AI photo rules, the instruction of her own photo / video note tool, and the service texts
  of a reply (`reply_rules`): the retry after the guard rejects a draft, the neutral reply of a
  handoff and the follow-up instruction;
- `mode` / `agent_prompt`: whether the persona runs on her saved single prompt ("unified") or on
  the prompt composed from her fields and the strategy ("legacy"), and the text the owner edits."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sim.domain.enums import ChannelKind, ClientStatus, MediaKind, Stage
from sim.services.ai_media import AI_PHOTO_RULE
from sim.services.call_prompt import call_identity, greeting
from sim.services.persona_media_tool import NO_MEDIA, Offer
from sim.services.prompt import DialogContext, PromptPart, StrategyProfile, dynamic_context
from sim.services.video_note import AI_VIDEO_RULE

EXAMPLE_CLIENT = "Имя клиента"
EXAMPLE_REF = "КОД-КЛИЕНТА"
LEGACY, UNIFIED = "legacy", "unified"


@dataclass(frozen=True)
class FixedRule:
    key: str
    title: str
    text: str


@dataclass(frozen=True)
class PromptMap:
    parts: list[PromptPart]
    context: str
    rules: list[FixedRule]
    strategy_id: int
    mode: str = LEGACY          # LEGACY | UNIFIED
    agent_prompt: str = ""      # the single prompt as the owner edits it (prompt.editable_agent_prompt)

    @property
    def system(self) -> str:
        """The stable system message exactly as the model gets it."""
        return "\n\n".join(part.text for part in self.parts)


def example_context(now: datetime, persona_facts: str, strategy: StrategyProfile) -> str:
    """The dynamic context of a first message from an unknown client, as of `now`."""
    return dynamic_context(DialogContext(
        stage=Stage.FIRST_CONTACT, status=ClientStatus.ACTIVE.value, client_name=EXAMPLE_CLIENT,
        channel_kind=ChannelKind.TELEGRAM.value, ref_code=EXAMPLE_REF, now=now,
        persona_facts=persona_facts,
    ), strategy)


def fixed_rules(persona_name: str) -> list[FixedRule]:
    return [
        FixedRule("call", "Правило звонков",
                  f"{call_identity(persona_name)}\n\n{greeting(persona_name)}"),
        FixedRule("video_note", "Правило кружочков", AI_VIDEO_RULE),
        FixedRule("ai_photo", "Правило фото ИИ", AI_PHOTO_RULE),
        FixedRule("media_tool", "Фото и кружочки по решению персонажа",
                  f"{Offer((MediaKind.PHOTO, MediaKind.VIDEO_NOTE)).brief()}\n\n"
                  f"Когда отправить нельзя, вместо этого:\n{NO_MEDIA}"),
    ]


def prompt_mode(agent_prompt: str | None) -> str:
    """A saved single prompt, even an edited-down one, is authoritative; NULL keeps the old way."""
    return LEGACY if agent_prompt is None else UNIFIED


def reply_rules(guard_retry: str, neutral_handoff: str, followup: str) -> list[FixedRule]:
    """The service texts code adds around a reply; the caller owns them (conversation, followup)."""
    return [
        FixedRule("guard_retry", "Повтор после отказа охранника", guard_retry),
        FixedRule("neutral_handoff", "Нейтральный ответ при передаче оператору", neutral_handoff),
        FixedRule("followup", "Напоминание молчащему клиенту (пример первого шага)", followup),
    ]
