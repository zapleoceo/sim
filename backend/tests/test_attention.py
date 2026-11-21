"""«Внимание» as data (sim/domain/attention.py, docs/attention.md): the owner's settings with
their defaults and broken values, and the strict JSON of the attention agent."""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from sim.domain.attention import (
    PACE_MAX,
    PACE_MIN,
    AttentionAction,
    AttentionMode,
    AttentionPlan,
    AttentionSettings,
    ChatDecision,
    attention_settings,
)


def test_nothing_stored_is_like_a_living_person() -> None:
    for stored in (None, {}, "garbage"):
        settings = attention_settings(stored)  # type: ignore[arg-type]
        assert settings == AttentionSettings()
    assert AttentionSettings().as_json() == {
        "mode": "human", "pace": 1.0, "may_leave_read": True, "after_missed_call": True,
        "night_dnd": True}


@pytest.mark.parametrize(("stored", "expected"), [
    ({"mode": "instant"}, AttentionSettings(mode=AttentionMode.INSTANT)),
    ({"mode": "free_only", "pace": 2}, AttentionSettings(mode=AttentionMode.FREE_ONLY,
                                                         pace=2.0)),
    ({"mode": "robot", "pace": 9}, AttentionSettings(pace=PACE_MAX)),     # broken → defaults
    ({"pace": 0.1}, AttentionSettings(pace=PACE_MIN)),
    ({"pace": True, "night_dnd": "no"}, AttentionSettings()),
    ({"may_leave_read": False, "after_missed_call": False, "night_dnd": False},
     AttentionSettings(may_leave_read=False, after_missed_call=False, night_dnd=False)),
])
def test_stored_values_are_read_leniently(stored: dict[str, Any],
                                          expected: AttentionSettings) -> None:
    assert attention_settings(stored) == expected


def test_instant_for_the_persona_or_for_one_chat() -> None:
    assert AttentionSettings(mode=AttentionMode.INSTANT).is_instant()
    assert AttentionSettings().is_instant(chat_instant=True)
    assert not AttentionSettings(mode=AttentionMode.FREE_ONLY).is_instant()


def test_a_decision_is_strict_but_forgiving_about_trivia() -> None:
    decision = ChatDecision.model_validate({
        "chat_id": 7, "action": "reply_later", "later": " 18:40 ",
        "reason": "  на рынке,\n ответит дома " + "очень " * 40})
    assert decision.action == AttentionAction.REPLY_LATER and decision.later == "18:40"
    assert decision.reason.startswith("на рынке, ответит дома") and len(decision.reason) == 120
    assert ChatDecision.model_validate({"chat_id": 1, "action": "read_only",
                                        "later": "null"}).later is None
    for bad in ({"chat_id": 1, "action": "sleep"},
                {"chat_id": 1, "action": "reply_later", "later": "25:00"},
                {"chat_id": 1, "action": "reply_now", "mood": "ok"}):
        with pytest.raises(ValidationError):
            ChatDecision.model_validate(bad)


def test_the_plan_schema_is_one_object_with_decisions() -> None:
    schema = AttentionPlan.model_json_schema()
    assert schema["type"] == "object" and "decisions" in schema["properties"]
    assert AttentionPlan.model_validate({"decisions": []}).decisions == []
