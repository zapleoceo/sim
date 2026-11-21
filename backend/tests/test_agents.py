"""The agent registry and its one accessor (sim/domain/agents.py, docs/agents.md): defaults,
fallbacks, broken stored values, the stored form of an edit, prompt placeholders and the
four sections of the vision prompt."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from sim.domain.agent_prompts import (
    GUARD_PROMPT,
    VISION_PROMPT,
    VISION_SECTIONS,
    render_prompt,
    vision_sections,
)
from sim.domain.agents import (
    AGENTS,
    DEFAULT_MAX_TOKENS,
    REGISTRY,
    AgentGroup,
    AgentKey,
    KeyTier,
    agent_config,
    clean_override,
)


def persona(agents: dict[str, Any] | None) -> Any:
    return SimpleNamespace(agents=agents)


def test_registry_covers_every_agent_once_in_the_owners_groups() -> None:
    assert [spec.key for spec in AGENTS] == list(AgentKey)
    groups = {group: [s.key.value for s in AGENTS if s.group == group] for group in AgentGroup}
    assert groups == {
        AgentGroup.REPLY: ["reply", "guard", "vision", "transcribe", "tts"],
        AgentGroup.MEDIA: ["image", "video"],
        AgentGroup.CALLS: ["live"],
        AgentGroup.BACKGROUND: ["life", "passport", "memory", "appearance"],
    }
    # Fast agents of the answer, media and calls run on paid keys; the background on «auto».
    for spec in AGENTS:
        expected = KeyTier.AUTO if spec.group == AgentGroup.BACKGROUND else KeyTier.PAID
        assert spec.key_tier == expected, spec.key
    assert REGISTRY["life"].model == "codex-sub/gpt-6.1-sol"
    for spec in AGENTS:
        assert spec.fallback is None or spec.fallback in REGISTRY
        assert not spec.placeholders or spec.has_prompt


def test_defaults_when_nothing_is_stored() -> None:
    for stored in (None, {}, {"reply": "garbage"}):
        config = agent_config(persona(stored), AgentKey.REPLY)
        assert (config.model, config.key_tier, config.prompt, config.max_tokens) == (
            "", KeyTier.PAID, "", 4096)
    guard = agent_config(persona(None), AgentKey.GUARD)
    assert (guard.prompt, guard.max_tokens) == (GUARD_PROMPT, 400)
    life = agent_config(persona(None), AgentKey.LIFE)
    assert (life.model, life.key_tier, life.max_tokens) == (
        "codex-sub/gpt-6.1-sol", KeyTier.AUTO, 6000)
    assert agent_config(persona(None), AgentKey.TTS).max_tokens == DEFAULT_MAX_TOKENS


def test_fallback_models_follow_the_chain() -> None:
    only_reply = persona({"reply": {"model": "r"}})
    assert agent_config(only_reply, AgentKey.GUARD).model == "r"
    assert agent_config(only_reply, AgentKey.MEMORY).model == "r"
    assert agent_config(only_reply, AgentKey.PASSPORT).model == "r"      # passport → memory
    with_memory = persona({"reply": {"model": "r"}, "memory": {"model": "m"}})
    assert agent_config(with_memory, AgentKey.PASSPORT).model == "m"
    # An explicit "" of an agent without a default model is «by default», i.e. the fallback.
    assert agent_config(persona({"reply": {"model": "r"}, "guard": {"model": ""}}),
                        AgentKey.GUARD).model == "r"
    seeing = persona({"vision": {"model": "v"}})
    assert agent_config(seeing, AgentKey.APPEARANCE).model == "v"
    assert agent_config(seeing, AgentKey.VISION).model == "v"


def test_the_planner_can_be_switched_off() -> None:
    off = persona({"life": {"model": ""}})
    assert agent_config(off, AgentKey.LIFE).model == ""
    own = persona({"life": {"model": " google/life "}})
    assert agent_config(own, AgentKey.LIFE).model == "google/life"


def test_owner_settings_and_broken_values() -> None:
    stored = persona({"guard": {"model": "g", "key_tier": "free", "prompt": "Проверь.",
                                "max_tokens": 300}})
    config = agent_config(stored, AgentKey.GUARD)
    assert (config.model, config.key_tier, config.prompt, config.max_tokens) == (
        "g", KeyTier.FREE, "Проверь.", 300)
    broken = persona({"guard": {"key_tier": "cheap", "prompt": "   ", "max_tokens": 10},
                      "tts": {"prompt": "speak", "max_tokens": 900}})
    config = agent_config(broken, AgentKey.GUARD)
    assert (config.key_tier, config.prompt, config.max_tokens) == (
        KeyTier.PAID, GUARD_PROMPT, 400)
    tts = agent_config(broken, AgentKey.TTS)
    assert (tts.prompt, tts.max_tokens) == ("", DEFAULT_MAX_TOKENS)   # not tunable: ignored
    assert agent_config(persona({"guard": {"max_tokens": True}}),
                        AgentKey.GUARD).max_tokens == 400


@pytest.mark.parametrize(("key", "sent", "stored"), [
    ("guard", {"model": None, "key_tier": "paid", "prompt": "", "max_tokens": 400}, {}),
    ("guard", {"model": "g", "key_tier": "free", "prompt": " мой ", "max_tokens": 500},
     {"model": "g", "key_tier": "free", "prompt": " мой ", "max_tokens": 500}),
    ("guard", {"prompt": GUARD_PROMPT + "\n"}, {}),                  # the default, re-sent
    ("guard", {"model": ""}, {}),                                    # "" = by default here
    ("life", {"model": ""}, {"model": ""}),                          # but «off» here
    ("life", {"model": "  x/y "}, {"model": "x/y"}),
    ("tts", {"prompt": "speak", "max_tokens": 900, "key_tier": "auto"}, {"key_tier": "auto"}),
])
def test_clean_override_keeps_only_what_differs(key: str, sent: dict[str, Any],
                                                stored: dict[str, Any]) -> None:
    assert clean_override(REGISTRY[key], sent) == stored


def test_render_prompt_fills_only_known_placeholders() -> None:
    text = 'Ты {name}, язык {language}. JSON: {"ok": true} {unknown}'
    assert render_prompt(text, name="Марина", language="ru") == (
        'Ты Марина, язык ru. JSON: {"ok": true} {unknown}')
    rendered = render_prompt(GUARD_PROMPT, name="Марина", language="ru")
    assert "Марина" in rendered and '{"ok": true или false' in rendered


def test_vision_sections() -> None:
    defaults = {key: text for key, (_, text) in VISION_SECTIONS.items()}
    assert vision_sections(VISION_PROMPT) == defaults
    assert vision_sections("") == defaults
    own = vision_sections("вступление\n[фото]\nКоротко.\n[Заметка]\nещё строка\n\n[PDF]\n  ")
    assert own["photo"] == "Коротко.\n[Заметка]\nещё строка"      # unknown header stays in
    assert own["pdf"] == defaults["pdf"]                             # empty → default
    assert own["video"] == defaults["video"] and own["video_note"] == defaults["video_note"]
