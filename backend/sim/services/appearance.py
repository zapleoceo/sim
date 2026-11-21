"""Her exact appearance in words (`persona.appearance`, docs/persona-life.md): the agent
«Портретист» (`appearance`, docs/agents.md; by default the vision model) looks at the reference
photos and writes a precise English description for image models (eye colour, hair, face, skin,
build, apparent age, usual make-up and jewellery). The owner edits it and saves; every generated
photo and video then carries it, so the eyes do not drift."""
from __future__ import annotations

from collections.abc import Sequence

from sim.domain.agents import AgentConfig
from sim.domain.enums import LlmPurpose
from sim.ports import ChatMessage, ChatPart
from sim.services.deps import Deps
from sim.services.llm_log import describe_messages, logged_call


def appearance_messages(prompt: str,
                        references: Sequence[tuple[bytes, str]]) -> list[ChatMessage]:
    parts = [ChatPart(text=prompt)]
    parts += [ChatPart(data=data, mime=mime) for data, mime in references]
    return [ChatMessage(role="user", parts=parts)]


async def describe_appearance(deps: Deps, *, agent: AgentConfig,
                              references: Sequence[tuple[bytes, str]]) -> str:
    messages = appearance_messages(agent.prompt, references)
    result = await logged_call(
        deps.sessions, purpose=LlmPurpose.VISION, model=agent.model,
        request={"messages": describe_messages(messages), "task": "appearance"},
        call=lambda: deps.llm.chat(model=agent.model, messages=messages,
                                   max_tokens=agent.max_tokens, label="appearance",
                                   key_tier=agent.key_tier),
        key_tier=agent.key_tier,
    )
    return " ".join(result.text.split())
