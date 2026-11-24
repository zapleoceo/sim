"""The persona's internal agents (docs/agents.md): one entry per AI function — who it is, which
Broker port it calls, which models fit it, the default key tier, model, prompt and max_tokens.

Per persona, the owner's overrides live in `persona.agents` (JSONB):
`{agent_key: {"model", "key_tier", "prompt", "max_tokens"}}`, every field optional. The one
accessor `agent_config(persona, key)` applies the registry defaults; services never read
`persona.agents` themselves.

Model resolution: the stored `model`, else the registry default ("" is «не использовать» only
where the default is a model, elsewhere it is the default); an empty result borrows the model of
the `fallback` agent (the guard and the chronicler use the companion's model, the passport writer
the chronicler's, the portraitist the vision model's)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from sim.domain.agent_prompts import (
    APPEARANCE_PROMPT,
    ATTENTION_PROMPT,
    GUARD_PROMPT,
    MEMORY_PROMPT,
    PASSPORT_PROMPT,
    PLANNER_PROMPT,
    VISION_PROMPT,
)

MIN_MAX_TOKENS = 64
MAX_MAX_TOKENS = 16_384             # the Broker's accepted range for chat jobs ends here
DEFAULT_MAX_TOKENS = 4096
# The subscription model of the night agents (planner, chronicler, passport writer): speed
# does not matter at night, and subscription calls spend no Broker keys.
NIGHT_MODEL = "codex-sub/gpt-6.1-sol"
MAX_PROMPT_CHARS = 50_000
HEYGEN_VIDEO_MODEL = "heygen/heygen-video-1"


class KeyTier(StrEnum):
    """Which Broker keys pay for a call. Subscription models (claude-sub/, codex-sub/) ignore it."""

    AUTO = "auto"           # the Broker decides (the field is not sent)
    PAID = "paid"           # paid keys only: fast and reliable
    FREE = "free"           # free keys only: slower, may be refused at a quota


class VideoProvider(StrEnum):
    GOOGLE = "google"
    HEYGEN = "heygen"


class AgentKey(StrEnum):
    """The keys of `persona.agents`; also the order of the registry."""

    ATTENTION = "attention"
    REPLY = "reply"
    GUARD = "guard"
    VISION = "vision"
    TRANSCRIBE = "transcribe"
    TTS = "tts"
    IMAGE = "image"
    VIDEO = "video"
    LIVE = "live"
    LIFE = "life"
    PASSPORT = "passport"
    MEMORY = "memory"
    APPEARANCE = "appearance"


class AgentGroup(StrEnum):
    REPLY = "reply"             # takes part in the answer, must be fast
    MEDIA = "media"             # generated photos and videos
    CALLS = "calls"             # live voice calls
    BACKGROUND = "background"   # runs on its own schedule, nobody waits for it


class BrokerPort(StrEnum):
    """The SIM port (sim/ports.py) an agent calls."""

    CHAT = "chat"               # LlmPort.chat
    TRANSCRIBE = "transcribe"   # TranscriberPort
    SPEECH = "speech"           # SpeechPort
    IMAGE = "image"             # ImageGenPort
    VIDEO = "video"             # VideoGenPort
    LIVE = "live"               # LivePort


@dataclass(frozen=True)
class ModelMatch:
    """A Broker model fits when it has ALL of these input and output modalities and
    capabilities. An agent fits a model when ANY of its matches does (dashboard filter)."""

    input: tuple[str, ...] = ()
    output: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()

    def as_json(self) -> dict[str, list[str]]:
        return {"input": list(self.input), "output": list(self.output),
                "capabilities": list(self.capabilities)}


TEXT_TO_TEXT = (ModelMatch(input=("text",), output=("text",)),)
SEES = (ModelMatch(input=("image",)), ModelMatch(input=("video",)))


@dataclass(frozen=True)
class AgentSpec:
    key: AgentKey
    title: str
    group: AgentGroup
    description: str
    port: BrokerPort
    model_filter: tuple[ModelMatch, ...]
    key_tier: KeyTier
    model: str = ""                         # default model ("" = the owner must choose)
    fallback: AgentKey | None = None        # whose model is used when this one has none
    prompt: str | None = None               # default of the editable prompt; None = none
    placeholders: tuple[str, ...] = ()      # `{name}` values the code fills into the prompt
    max_tokens: int | None = None           # default answer limit; None = not tunable

    @property
    def has_prompt(self) -> bool:
        return self.prompt is not None


GROUP_TITLES: dict[AgentGroup, tuple[str, str]] = {
    AgentGroup.REPLY: ("Ответ", "Участвуют в каждом ответе и должны отвечать быстро."),
    AgentGroup.MEDIA: ("Медиа", "Новые фото, видео и кружки."),
    AgentGroup.CALLS: ("Звонки", "Живой голосовой разговор."),
    AgentGroup.BACKGROUND: ("Фон", "Работают сами по себе, ответа никто не ждёт."),
}

AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(AgentKey.ATTENTION, "Внимание", AgentGroup.REPLY,
              "Решает, когда она берёт телефон и что делает с уведомлениями: отвечает сразу, "
              "позже, только читает или не открывает.",
              BrokerPort.CHAT, TEXT_TO_TEXT, KeyTier.PAID, model="gemini/gemini-3.5-flash-lite",
              prompt=ATTENTION_PROMPT, placeholders=("name",), max_tokens=1500),
    AgentSpec(AgentKey.REPLY, "Собеседник", AgentGroup.REPLY,
              "Пишет ответ клиенту: JSON-план из текстов, голосовых, фото и видео.",
              BrokerPort.CHAT, TEXT_TO_TEXT, KeyTier.PAID, max_tokens=4096),
    AgentSpec(AgentKey.GUARD, "Охранник", AgentGroup.REPLY,
              "Проверяет ответ перед отправкой по правилам и списку запретов.",
              BrokerPort.CHAT, TEXT_TO_TEXT, KeyTier.PAID, fallback=AgentKey.REPLY,
              prompt=GUARD_PROMPT, placeholders=("name", "language"), max_tokens=400),
    AgentSpec(AgentKey.VISION, "Зрение", AgentGroup.REPLY,
              "Смотрит фото, видео, кружки и PDF клиента и описывает их словами.",
              BrokerPort.CHAT, SEES, KeyTier.PAID, prompt=VISION_PROMPT, max_tokens=800),
    AgentSpec(AgentKey.TRANSCRIBE, "Слух", AgentGroup.REPLY,
              "Расшифровывает голосовые и звук кружков клиента.",
              BrokerPort.TRANSCRIBE, (ModelMatch(input=("audio",), output=("text",)),),
              KeyTier.PAID),
    AgentSpec(AgentKey.TTS, "Голос", AgentGroup.REPLY,
              "Озвучивает её голосовые сообщения.",
              BrokerPort.SPEECH, (ModelMatch(output=("audio",)),), KeyTier.PAID),
    AgentSpec(AgentKey.IMAGE, "Фотограф", AgentGroup.MEDIA,
              "Генерирует её новые фото по референсам внешности и сцене.",
              BrokerPort.IMAGE, (ModelMatch(output=("image",)),), KeyTier.PAID),
    AgentSpec(AgentKey.VIDEO, "Видеограф", AgentGroup.MEDIA,
              "Оживляет фото в видео и кружки (нужен и Фотограф).",
              BrokerPort.VIDEO, (ModelMatch(output=("video",)),), KeyTier.PAID),
    AgentSpec(AgentKey.LIVE, "Звонок", AgentGroup.CALLS,
              "Говорит её голосом в звонке в реальном времени.",
              BrokerPort.LIVE, (ModelMatch(capabilities=("live",)),
                                ModelMatch(input=("audio",), output=("audio",))),
              KeyTier.PAID),
    AgentSpec(AgentKey.LIFE, "Планировщик", AgentGroup.BACKGROUND,
              "Раз в день планирует её день: события, одежду, истории.",
              BrokerPort.CHAT, TEXT_TO_TEXT, KeyTier.AUTO, model=NIGHT_MODEL,
              prompt=PLANNER_PROMPT, placeholders=("name", "ambience", "summary_rule"),
              max_tokens=6000),
    AgentSpec(AgentKey.PASSPORT, "Паспортистка", AgentGroup.BACKGROUND,
              "Ночью раз в сутки обновляет паспорт клиента по итогам дня (и по кнопке "
              "«Обновить паспорт сейчас»).",
              BrokerPort.CHAT, TEXT_TO_TEXT, KeyTier.AUTO, model=NIGHT_MODEL,
              prompt=PASSPORT_PROMPT, placeholders=("name",), max_tokens=6000),
    AgentSpec(AgentKey.MEMORY, "Летописец", AgentGroup.BACKGROUND,
              "Дневник дня и недели: в её полночь пишет подробный итог дня каждого чата, в "
              "ночь на понедельник — итог недели.",
              BrokerPort.CHAT, TEXT_TO_TEXT, KeyTier.AUTO, model=NIGHT_MODEL,
              prompt=MEMORY_PROMPT, placeholders=("name",), max_tokens=8000),
    AgentSpec(AgentKey.APPEARANCE, "Портретист", AgentGroup.BACKGROUND,
              "По референсам описывает её внешность для фото и видео («Описать по "
              "референсам»).",
              BrokerPort.CHAT, (ModelMatch(input=("image",)),), KeyTier.AUTO,
              fallback=AgentKey.VISION, prompt=APPEARANCE_PROMPT, max_tokens=800),
)
REGISTRY: dict[str, AgentSpec] = {spec.key: spec for spec in AGENTS}


@dataclass(frozen=True)
class AgentConfig:
    """What a service needs for one call: the resolved model ("" = not configured), the key
    tier, the prompt (the owner's or the default; "" for agents without one) and max_tokens
    (`DEFAULT_MAX_TOKENS` for agents where it is not tunable)."""

    model: str
    key_tier: KeyTier
    prompt: str
    max_tokens: int


class HasAgents(Protocol):
    @property
    def agents(self) -> Mapping[str, Any] | None: ...


def overrides_of(persona: HasAgents, key: str) -> Mapping[str, Any]:
    stored = (persona.agents or {}).get(key)
    return stored if isinstance(stored, Mapping) else {}


def video_provider(persona: HasAgents) -> VideoProvider:
    return (VideoProvider.HEYGEN
            if overrides_of(persona, AgentKey.VIDEO).get("video_provider") == VideoProvider.HEYGEN
            else VideoProvider.GOOGLE)


def _model(persona: HasAgents, spec: AgentSpec, seen: frozenset[str]) -> str:
    if spec.key == AgentKey.VIDEO and video_provider(persona) == VideoProvider.HEYGEN:
        return HEYGEN_VIDEO_MODEL
    own = own_model(spec, overrides_of(persona, spec.key))
    model = spec.model if own is None else own
    if not model and spec.fallback and spec.fallback not in seen:
        return _model(persona, REGISTRY[spec.fallback], seen | {spec.key})
    return model


def valid_tier(value: Any) -> KeyTier | None:
    try:
        return KeyTier(value)
    except ValueError:
        return None


def valid_max_tokens(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if MIN_MAX_TOKENS <= value <= MAX_MAX_TOKENS else None


def own_prompt(spec: AgentSpec, stored: Mapping[str, Any]) -> str | None:
    """The owner's prompt of this agent, or None when it is the default one."""
    prompt = stored.get("prompt")
    if not spec.has_prompt or not isinstance(prompt, str) or not prompt.strip():
        return None
    return None if prompt.strip() == (spec.prompt or "").strip() else prompt


def own_model(spec: AgentSpec, stored: Mapping[str, Any]) -> str | None:
    """The owner's model choice, or None for «by default». "" is kept only where it differs
    from the default (an agent with a default model switched off)."""
    model = stored.get("model")
    if not isinstance(model, str):
        return None
    model = model.strip()
    return None if not model and not spec.model else model


def agent_config(persona: HasAgents, key: str) -> AgentConfig:
    """The persona's settings of agent `key` with the registry defaults applied. Broken stored
    values (an unknown tier, max_tokens out of range) fall back to the defaults."""
    spec = REGISTRY[key]
    stored = overrides_of(persona, key)
    tokens = valid_max_tokens(stored.get("max_tokens")) if spec.max_tokens else None
    return AgentConfig(
        model=_model(persona, spec, frozenset()),
        key_tier=valid_tier(stored.get("key_tier")) or spec.key_tier,
        prompt=own_prompt(spec, stored) or spec.prompt or "",
        max_tokens=tokens or spec.max_tokens or DEFAULT_MAX_TOKENS,
    )


def clean_override(spec: AgentSpec, data: Mapping[str, Any]) -> dict[str, Any]:
    """The stored form of one agent's settings: only what differs from «by default» (a model
    choice, a tier other than the default, the owner's own prompt, another max_tokens)."""
    out: dict[str, Any] = {}
    model = own_model(spec, data)
    if model is not None:
        out["model"] = model
    tier = valid_tier(data.get("key_tier"))
    if tier is not None and tier != spec.key_tier:
        out["key_tier"] = tier.value
    prompt = own_prompt(spec, data)
    if prompt is not None:
        out["prompt"] = prompt
    tokens = valid_max_tokens(data.get("max_tokens")) if spec.max_tokens else None
    if tokens is not None and tokens != spec.max_tokens:
        out["max_tokens"] = tokens
    if spec.key == AgentKey.VIDEO and data.get("video_provider") == VideoProvider.HEYGEN:
        out["video_provider"] = VideoProvider.HEYGEN.value
    return out
