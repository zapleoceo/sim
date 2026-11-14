"""The map of a persona's prompt for the admin: every piece the model gets, where it comes from
and where the owner edits it. Pure functions, no IO.

- `parts`: the stable system prompt piece by piece (`sim.services.prompt.system_prompt_parts`);
- `context`: an example of the dynamic message code builds for every reply, with her real facts
  for this moment (age from the birth date, local time, where she is by her routine) and
  placeholder client data;
- `rules`: what is fixed in the code and not editable: the phone call rule, the AI video note
  and AI photo rules, and the instruction of her own photo / video note tool."""
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
