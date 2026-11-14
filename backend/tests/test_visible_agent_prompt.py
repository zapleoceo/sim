"""Single behavioural source and request snapshots without paid/model calls."""
from dataclasses import replace
from datetime import datetime

import pytest

from sim.domain.enums import Stage
from sim.ports.llm import LLMResult
from sim.services.call_prompt import call_system_prompt
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
    system_prompt_parts,
)
from sim.services.prompt_map import prompt_mode, reply_rules


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
    assert "дружелюбное" not in text and "24" in text and "1 из 1" in text and "Последнее: да" in text


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


def test_unified_parts_are_her_text_the_link_rule_and_the_answer_format():
    p = PersonaProfile(name="name", description="OLD", prohibitions="OLD BAN", routine="пн 09:00-10:00 OLD",
                       agent_prompt="only this")
    parts = system_prompt_parts(p, StrategyProfile(prompt="OLD STRATEGY"), PromptTexts(contract="JSON"))
    assert [(x.key, x.source, x.field, x.text) for x in parts] == [
        ("agent_prompt", "persona", "agent_prompt", "only this"),
        ("link_contract", "code", "", LINK_CONTRACT),
        ("output_contract", "setting", "output_contract", "JSON"),
    ]
    # An emptied-down saved text is still authoritative: only NULL means legacy.
    assert system_prompt_parts(replace(p, agent_prompt=" "), StrategyProfile())[0].key == "agent_prompt"
    assert prompt_mode(None) == "legacy" and prompt_mode("") == "unified"


def test_a_phone_call_starts_from_the_unified_text_too():
    p = PersonaProfile(name="Марина", description="OLD DESCRIPTION", agent_prompt="only this")
    ctx = DialogContext(Stage.ENGAGED, "active", "client", "telegram_user", "REF", datetime(2026, 1, 1))
    text = call_system_prompt(p, StrategyProfile(prompt="OLD STRATEGY"), [], ctx)
    assert text.startswith(f"only this\n\n{LINK_CONTRACT}\n\n") and "OLD" not in text


def test_reply_rules_show_the_service_texts_of_a_reply():
    rules = reply_rules("retry {feedback}", "handoff", followup_instruction([24], 0))
    assert [(r.key, r.text) for r in rules][:2] == [("guard_retry", "retry {feedback}"),
                                                    ("neutral_handoff", "handoff")]
    assert rules[2].key == "followup" and "ЗАДАЧА НАПОМИНАНИЯ" in rules[2].text
