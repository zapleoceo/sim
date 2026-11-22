"""Her mood of the day (docs/persona-life.md, «Настроение дня»; docs/writing.md, «Настроение»):
the planner puts a mood with its reason on every event, the scene and the «now» block carry it,
and the style block tells the reply how it shows. Calm says nothing."""
from __future__ import annotations

from datetime import datetime

from sim.domain.agent_prompts import PLANNER_PROMPT
from sim.domain.life import DayEvent, Mood
from sim.services.context.instructions import CALL_RULES
from sim.services.life.render import life_blocks
from sim.services.writing import MOOD_RULES, style_block, writing_mode
from tests.services.life_seed import SATURDAY, day_plan, event, life_state

EVENING = datetime(2026, 11, 21, 20, 20)


def moody_plan() -> list[DayEvent]:
    plan = day_plan()
    plan[-2] = event("19:00", "21:00", "ужин и сериал", place="дома", with_whom=["Андрей"],
                     outfit="серый домашний свитер и тёмные леггинсы", ambience="quiet_home",
                     mood="irritated", mood_why="Андрей опять не вынес мусор")
    return plan


def test_an_event_is_calm_unless_the_planner_says_otherwise() -> None:
    plain = event("09:00", "10:00", "кофе")
    assert (plain.mood, plain.mood_why) == (Mood.CALM, "")
    assert "mood_why" in PLANNER_PROMPT and "irritated" in PLANNER_PROMPT


def test_the_scene_and_the_now_block_carry_her_mood() -> None:
    state = life_state("19:30", plans={SATURDAY: moody_plan()})
    scene = state.scene()
    assert (scene.mood, scene.mood_why) == (Mood.IRRITATED, "Андрей опять не вынес мусор")
    assert "Настроение: раздражена, не в духе — Андрей опять не вынес мусор." in (
        life_blocks(state).now)
    calm = life_state("19:30", plans={SATURDAY: day_plan()})
    assert calm.scene().mood == Mood.CALM and "Настроение:" not in life_blocks(calm).now


def test_a_gap_keeps_the_mood_of_the_event_before_it() -> None:
    gap = life_state("21:10", plans={SATURDAY: moody_plan()}).scene()
    assert gap.activity == "свободное время" and gap.mood == Mood.IRRITATED


def test_the_style_block_says_how_the_mood_shows() -> None:
    scene = life_state("19:30", plans={SATURDAY: moody_plan()}).scene()
    writing = writing_mode(scene, EVENING)
    assert writing.mood == Mood.IRRITATED
    block = style_block(writing)
    assert "Ты не в духе (Андрей опять не вынес мусор)" in block
    assert "огрызаешься" in block and "без смайликов" in block
    calm = style_block(writing_mode(life_state("19:30", plans={SATURDAY: day_plan()}).scene(),
                                    EVENING))
    assert not any(rule.split("{")[0] in calm for rule in MOOD_RULES.values())
    assert set(MOOD_RULES) == set(Mood) - {Mood.CALM}


def test_a_call_never_switches_language_on_noise() -> None:
    # 2026-11-22: her «алло» was heard as Portuguese, she offered to pick a language.
    assert "помехи" in CALL_RULES and "не предлагай выбрать язык" in CALL_RULES
