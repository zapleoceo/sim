"""She writes and calls first, as pure rules (sim/domain/initiative.py, docs/initiative.md):
when she may call herself, how a due message reads in her timeline, the plan's `call`; and her
speed of speech (sim/domain/speech.py)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from sim.domain.initiative import (
    CALL_GAP,
    CALLS_PER_DAY,
    NO_ANSWER_RESULTS,
    NO_ANSWER_TOPIC,
    CallOutState,
    call_out_block,
    initiative_line,
)
from sim.domain.plan import ReplyPlan
from sim.domain.speech import MAX_SPEED, MIN_SPEED, clamp_speed, is_normal

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def state(**fields: Any) -> CallOutState:
    values: dict[str, Any] = {"switched_on": True, "unavailable": "", "local": NOW,
                              "last_call_at": None, "calls_today": 0, "pending": False,
                              "now": NOW}
    return CallOutState(**{**values, **fields})


def at_hour(hour: int, minute: int = 0) -> datetime:
    return NOW.replace(hour=hour, minute=minute)


def test_she_may_call_in_the_day_when_nothing_holds_her_back() -> None:
    assert call_out_block(state()) == ""
    assert call_out_block(state(local=at_hour(9))) == ""
    assert call_out_block(state(local=at_hour(21, 59))) == ""
    assert call_out_block(state(last_call_at=NOW - CALL_GAP)) == ""
    assert call_out_block(state(calls_today=CALLS_PER_DAY - 1)) == ""


@pytest.mark.parametrize(("fields", "reason"), [
    ({"switched_on": False}, "в этом чате звонить самой нельзя"),
    # The switch is named first even when the calls do not work at all.
    ({"switched_on": False, "unavailable": "звонки выключены на сервере"},
     "в этом чате звонить самой нельзя"),
    ({"unavailable": "звонки есть только в Telegram"}, "звонки есть только в Telegram"),
    ({"pending": True}, "ты уже набираешь его"),
    ({"local": at_hour(22)}, "сейчас слишком поздно или рано для звонка"),
    ({"local": at_hour(23, 30)}, "сейчас слишком поздно или рано для звонка"),
    ({"local": at_hour(3)}, "сейчас слишком поздно или рано для звонка"),
    ({"local": at_hour(8, 59)}, "сейчас слишком поздно или рано для звонка"),
    ({"last_call_at": NOW - CALL_GAP + timedelta(minutes=1)}, "ты недавно уже звонила"),
    ({"calls_today": CALLS_PER_DAY}, "сегодня ты уже звонила достаточно"),
])
def test_why_she_must_not_call_now(fields: dict[str, Any], reason: str) -> None:
    assert call_out_block(state(**fields)) == reason


def test_a_due_message_reads_as_her_own_idea() -> None:
    assert initiative_line("спросить, как прошло собеседование") == (
        "(ты решила написать первой: спросить, как прошло собеседование)")


def test_a_missed_call_of_hers_asks_her_to_explain_it() -> None:
    topic = NO_ANSWER_TOPIC.format(topic="договорились созвониться")
    assert "(договорились созвониться)" in topic and "не взял трубку" in topic
    assert NO_ANSWER_RESULTS == {"no_answer": "не взял трубку", "declined": "сбросил звонок",
                                 "busy": "занято"}


def test_the_plan_may_carry_her_call() -> None:
    plan = ReplyPlan.model_validate({"messages": [], "call": {"topic": "созвониться"}})
    assert plan.call is not None and plan.call.topic == "созвониться"
    assert ReplyPlan.model_validate({"messages": []}).call is None
    for bad in ({"topic": ""}, {"topic": "x" * 301}, {"topic": "а", "when": "сейчас"}):
        with pytest.raises(ValidationError):
            ReplyPlan.model_validate({"messages": [], "call": bad})
    assert "call" in ReplyPlan.model_json_schema()["properties"]


# --- her speed of speech -------------------------------------------------------------------------


@pytest.mark.parametrize(("stored", "speed"), [
    (None, 1.0), (0.0, 1.0), (1.0, 1.0), (1.25, 1.25), (1.234, 1.23),
    (0.5, MIN_SPEED), (3.0, MAX_SPEED), (0.8, 0.8), (1.5, 1.5),
])
def test_clamp_speed_keeps_the_forms_range(stored: float | None, speed: float) -> None:
    assert clamp_speed(stored) == speed


def test_normal_speed_is_the_models_own_pace() -> None:
    assert is_normal(1.0) and is_normal(0.0) and is_normal(1.004)
    assert not is_normal(1.3) and not is_normal(0.9) and not is_normal(0.5)
