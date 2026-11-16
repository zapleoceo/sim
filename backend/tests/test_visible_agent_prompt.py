from datetime import datetime

from sim.domain.enums import Stage
from sim.services.call_prompt import call_system_prompt
from sim.services.followup import followup_instruction
from sim.services.guard import FACT_CONTROL, VOICE_CONTROL, guard_system_prompt
from sim.services.prompt import (
    LINK_CONTRACT,
    UNIFIED_BOUNDARIES,
    DialogContext,
    PersonaProfile,
    PromptTexts,
    StrategyProfile,
    Turn,
    build_messages,
    editable_agent_prompt,
    stable_system_prompt,
)


def test_unified_source_retains_mandatory_rules_dynamic_facts_and_voice_identity():
    p = PersonaProfile(name="Marina", description="OLD DESCRIPTION", agent_prompt=" exact\n ")
    s = StrategyProfile(prompt="OLD STRATEGY")
    ctx = DialogContext(Stage.ENGAGED, "active", "client", "preview", "REF", datetime(2026, 1, 1),
                        persona_facts="PROGRAMMATIC CLOCK 2026-01-01")
    messages = build_messages(p, s, [Turn("user", "hi")], ctx, texts=PromptTexts(contract="JSON"))
    assert messages[0]["content"] == "\n\n".join((p.agent_prompt, LINK_CONTRACT, UNIFIED_BOUNDARIES, "JSON"))
    assert "OLD" not in messages[0]["content"]
    assert "PROGRAMMATIC CLOCK" in messages[-1]["content"]
    voice = call_system_prompt(p, s, [], ctx)
    assert voice.startswith(p.agent_prompt) and "OLD" not in voice
    assert "PROGRAMMATIC CLOCK" in voice
    assert FACT_CONTROL in guard_system_prompt() and VOICE_CONTROL in guard_system_prompt()
    assert editable_agent_prompt(p) == p.agent_prompt


def test_editable_agent_prompt_returns_empty_when_none():
    p = PersonaProfile(name="name", description="description")
    assert editable_agent_prompt(p) == ""


def test_followup_references_unified_prompt():
    text = followup_instruction([24], 0)
    assert "24" in text and "единому промпту" in text
