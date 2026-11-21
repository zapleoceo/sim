"""Her exact appearance in words (`persona.appearance`, docs/persona-life.md): the vision model
looks at the reference photos and writes a precise English description for image models (eye
colour, hair, face, skin, build, apparent age, usual make-up and jewellery). The owner edits it
and saves; every generated photo and video then carries it, so the eyes do not drift."""
from __future__ import annotations

from collections.abc import Sequence

from sim.domain.enums import LlmPurpose
from sim.ports import ChatMessage, ChatPart
from sim.services.deps import Deps
from sim.services.llm_log import describe_messages, logged_call

APPEARANCE_MAX_TOKENS = 800

APPEARANCE_PROMPT = """\
These photos show the same woman. Describe her appearance precisely, in English, for an image \
generation model that must draw exactly her: eye colour, hair colour, length and usual style, \
face shape and features, eyebrows, skin tone and texture, build and height impression, \
apparent age, usual make-up, jewellery and other constant details (glasses, piercings, \
moles). Only what does not change between photos: no clothes, no background, no pose. \
One compact paragraph of short comma-separated phrases, no introduction."""


def appearance_messages(references: Sequence[tuple[bytes, str]]) -> list[ChatMessage]:
    parts = [ChatPart(text=APPEARANCE_PROMPT)]
    parts += [ChatPart(data=data, mime=mime) for data, mime in references]
    return [ChatMessage(role="user", parts=parts)]


async def describe_appearance(deps: Deps, *, model: str,
                              references: Sequence[tuple[bytes, str]]) -> str:
    messages = appearance_messages(references)
    result = await logged_call(
        deps.sessions, purpose=LlmPurpose.VISION, model=model,
        request={"messages": describe_messages(messages), "task": "appearance"},
        call=lambda: deps.llm.chat(model=model, messages=messages,
                                   max_tokens=APPEARANCE_MAX_TOKENS, label="appearance"),
    )
    return " ".join(result.text.split())
