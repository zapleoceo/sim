"""Single behavioural source and request snapshots without paid/model calls."""
from dataclasses import replace
from datetime import datetime

import pytest

from sim.domain.enums import Stage
from sim.ports.llm import LLMResult
from sim.services.followup import followup_instruction
from sim.services.llm_audit import AuditedLLM, capture_requests
from sim.services.prompt import (
    LINK_CONTRACT,
    DialogContext,
    PersonaProfile,
    PromptTexts,
    StrategyProfile,
    Turn,
    build_messages,
    editable_agent_prompt,
    stable_system_prompt,
)


def test_unified_prompt_is_authoritative_for_every_builder_caller():
    p = PersonaProfile(name="name", description="OLD DESCRIPTION", agent_prompt="  exact text\n ")
    s = StrategyProfile(name="OLD NAME", prompt="OLD STRATEGY", rules=["OLD RULE"])
    texts = PromptTexts(contract="JSON CONTRACT")
    ctx = DialogContext(Stage.ENGAGED, "active", "client", "preview", "REF", datetime(2026, 1, 1),
                        persona_facts="ROUTINE: current place and next event")
    messages = build_messages(p, s, [Turn("user", "hi")], ctx, texts=texts)
    assert messages[0]["content"] == "\n\n".join((p.agent_prompt, LINK_CONTRACT, texts.contract))
    assert "OLD" not in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "hi"}
    assert "client" in messages[-1]["content"]
    assert "ROUTINE: current place and next event" in messages[-1]["content"]
    assert editable_agent_prompt(p, s) == p.agent_prompt


def test_compatibility_materializes_existing_text_without_dropping_legacy_rules():
    p = PersonaProfile(name="name", description="description")
    s = StrategyProfile(prompt="strategy", rules=["legacy rule"])
    text = editable_agent_prompt(p, s)
    assert "description" in text and "strategy" in text and "legacy rule" in text
    assert text == stable_system_prompt(p, s, PromptTexts(contract="")).rstrip()
    assert editable_agent_prompt(replace(p, agent_prompt=text), s) == text


def test_unified_followup_is_an_event_not_hidden_style():
    assert "дружелюбное" in followup_instruction([24], 0)
    text = followup_instruction([24], 0, unified=True)
    assert "дружелюбное" not in text and "24" in text and "1 из 1" in text


class FakePort:
    async def chat(self, messages, **kwargs):
        messages[0]["content"] = "mutated by fake transport"
        return LLMResult(text="{}")


class NoDbAudit(AuditedLLM):
    async def record(self, call, started_at=None):
        pass


@pytest.mark.asyncio
async def test_capture_records_exact_copy_and_separate_guard_and_resets():
    audit = NoDbAudit(FakePort(), None)
    with capture_requests() as outer:
        await audit.chat([{"role": "system", "content": "generator"}], workflow="preview")
        with capture_requests() as inner:
            await audit.chat([{"role": "system", "content": "guard"}], workflow="guard")
        await audit.chat([{"role": "system", "content": "retry"}], workflow="preview")
    await audit.chat([{"role": "user", "content": "outside"}])
    assert [r["messages"][0]["content"] for r in outer] == ["generator", "retry"]
    assert inner[0]["workflow"] == "guard"
    assert inner[0]["messages"][0]["content"] == "guard"
