"""The persona's world and day as data: schema of the seed, day and outfit checks, the
calendar, the agenda layers, stable randomness and the context blocks (pure, no database).
When she answers by how busy she is: tests/services/test_attention_timing.py."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from sim.db.models import PersonaArc, PersonaCanon
from sim.domain.enums import ArcStatus, CanonStatus
from sim.domain.life import DayPlan, PersonaLife, day_errors, outfit_errors
from sim.services.life.agenda import build_agenda
from sim.services.life.calendar import date_facts, easter, holiday, repentance_day
from sim.services.life.render import (
    ARCS_HEADING,
    CANON_HEADING,
    NOW_HEADING,
    ROUTINE_HEADING,
    days_label,
    life_blocks,
)
from sim.services.life.stable import stable_fraction, stable_seed
from sim.services.life.world import life_of
from tests.services.life_seed import (
    SATURDAY,
    SEED,
    day_plan,
    event,
    life_state,
    persona,
)

# --- schema ------------------------------------------------------------------------------------


def test_the_owner_seed_is_accepted_as_is() -> None:
    life = PersonaLife.model_validate(SEED["life"])
    assert len(life.routine) == 5 and life.routine[0].busy == "away"
    assert life.people[0].name == "Андрей" and life.places[0].name == "дом"
    assert PersonaLife.model_validate({}).empty


@pytest.mark.parametrize("slot", [
    {"days": [7], "start": "10:00", "end": "11:00", "activity": "x"},
    {"days": [1], "start": "25:00", "end": "11:00", "activity": "x"},
    {"days": [1], "start": "10:00", "end": "10:00", "activity": "x"},
    {"days": [], "start": "10:00", "end": "11:00", "activity": "x"},
    {"days": [1], "start": "10:00", "end": "11:00", "activity": "x", "busy": "maybe"},
    {"days": [1], "start": "10:00", "end": "11:00", "activity": "x", "extra": 1},
])
def test_bad_routine_slots_are_rejected(slot: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PersonaLife.model_validate({"routine": [slot]})


def test_day_errors_catch_order_overlap_and_midnight() -> None:
    assert day_errors(day_plan()) == []
    overlap = [event("08:00", "09:30", "a"), event("09:00", "10:00", "b")]
    assert "без наложений" in day_errors(overlap)[0]
    midnight = [event("22:00", "01:00", "a"), event("09:00", "10:00", "b")]
    assert any("через полночь" in e for e in day_errors(midnight))
    assert day_errors([event("22:00", "01:00", "поздно")]) == []


def test_outfits_stay_the_same_without_a_reason() -> None:
    assert outfit_errors(day_plan()) == []
    home = [event("09:00", "10:00", "уборка", place="дома", outfit="серый свитер"),
            event("10:00", "11:00", "кофе", place="дома", outfit="красное платье")]
    [error] = outfit_errors(home)
    assert error.startswith("events[1]: одежда сменилась без причины")
    after_pause = [home[0], home[1].model_copy(update={"start": "10:30", "end": "11:00"})]
    assert outfit_errors(after_pause) == []
    going_out = [home[0], home[1].model_copy(update={"place": "кафе в центре"})]
    assert outfit_errors(going_out) == []
    missing = day_plan()
    missing[0] = missing[0].model_copy(update={"outfit": " "})
    assert outfit_errors(missing) == ["events[0]: не указано outfit"]


def test_planner_answer_needs_six_to_fourteen_events() -> None:
    events = [e.model_dump(mode="json") for e in day_plan()]
    with pytest.raises(ValidationError):
        DayPlan.model_validate({"events": events[:5]})
    assert len(DayPlan.model_validate({"events": events}).events) == 7


# --- calendar ----------------------------------------------------------------------------------


def test_easter_and_saxon_holidays() -> None:
    assert easter(2024) == date(2024, 3, 31)
    assert easter(2026) == date(2026, 4, 5)
    assert repentance_day(2026) == date(2026, 11, 18)
    assert "Реформации" in (holiday(date(2026, 10, 31)) or "")
    assert "Страстная пятница" in (holiday(date(2026, 4, 3)) or "")
    assert holiday(date(2026, 11, 19)) is None
    facts = date_facts(date(2026, 12, 24))
    assert facts[0].startswith("четверг, 24 декабря 2026, зима")
    assert "Сочельник" in facts[1] and "Рождество" in facts[2]


# --- agenda and busy rules ---------------------------------------------------------------------


def test_routine_fills_the_night_around_a_planned_day() -> None:
    life = life_of(persona())
    agenda = build_agenda(SATURDAY, life, {SATURDAY: day_plan()})
    assert agenda.planned
    first = agenda.slots[0]
    assert (first.start, first.end, first.title) == (0, 390, "сон, телефон в другой комнате")
    assert first.key == "routine:2026-11-20T23:00"         # the tail keeps Friday's key
    titles = [slot.title for slot in agenda.slots]
    assert "пробежка 5 км" not in titles                   # the plan wins its hours
    assert agenda.at(20 * 60 + 10).title == "ужин и сериал"  # type: ignore[union-attr]
    assert agenda.slots[-1].title == "сон, телефон в другой комнате"
    assert agenda.slots[-1].start == 23 * 60


def test_routine_alone_without_a_plan() -> None:
    agenda = build_agenda(SATURDAY, life_of(persona()), {})
    assert not agenda.planned
    assert agenda.at(20 * 60 + 10).title == "созвон с Соней"  # type: ignore[union-attr]
    assert agenda.at(19 * 60) is None


def test_stable_randomness() -> None:
    assert stable_fraction("a") == stable_fraction("a") != stable_fraction("b")
    assert 0 <= stable_fraction("x") < 1
    assert stable_seed("event-1") == stable_seed("event-1")


# --- context blocks ----------------------------------------------------------------------------


def test_days_label() -> None:
    assert days_label(range(7)) == "ежедневно"
    assert days_label([0, 1, 2, 3, 4]) == "пн–пт"
    assert days_label([0, 2, 4, 5]) == "пн, ср, пт, сб"
    assert days_label([5, 6]) == "сб, вс"


def test_blocks_with_a_planned_day() -> None:
    arc = PersonaArc(title="Ремонт в ванной", summary="Меняют ванну на душ.",
                     stage="плитка в коридоре", status=ArcStatus.ACTIVE)
    canon = PersonaCanon(fact="Соседскую собаку зовут Рекс", status=CanonStatus.ACTIVE)
    friday = SATURDAY - timedelta(days=1)
    life = life_state("19:30", plans={SATURDAY: day_plan(), friday: day_plan()},
                 arcs=[arc], canon=[canon])
    life.days[friday].summary = "Пятница прошла спокойно, вечером сериал."
    blocks = life_blocks(life)
    assert blocks.routine.startswith(ROUTINE_HEADING)
    assert "- ежедневно 23:00–06:30 сон, телефон в другой комнате (дома) [недоступна]" in (
        blocks.routine)
    assert "- Андрей — муж, с 2004;" in blocks.routine and "Места:" in blocks.routine
    now = blocks.now
    assert now.startswith(NOW_HEADING)
    assert "Сейчас 19:30, суббота, по плану дня." in now
    assert "Ты сейчас: ужин и сериал (дома; с Андрей), 19:00–21:00." in now
    assert "Занятость: свободна" in now
    assert "На тебе сейчас: серый домашний свитер и тёмные леггинсы." in now
    assert "- 08:00–09:00 душ и завтрак с Андреем (дома, кухня; с Андрей): Бублик стащил" in now
    assert "Дальше сегодня:\n- 21:30–23:00 чтение Канемана" in now
    assert "Вчера (пятница): Пятница прошла спокойно, вечером сериал." in now
    assert "Завтра (воскресенье, по распорядку):" in now
    assert blocks.arcs == (f"{ARCS_HEADING}\n- Ремонт в ванной: Меняют ванну на душ. "
                           "Сейчас: плитка в коридоре")
    assert blocks.canon.startswith(CANON_HEADING) and "- Соседскую собаку зовут Рекс" in (
        blocks.canon)
    assert blocks.texts() == [blocks.routine, blocks.now, blocks.arcs, blocks.canon]
    assert blocks.shared() == [blocks.routine, blocks.arcs, blocks.canon]      # no «now»


def test_blocks_fall_back_to_the_routine() -> None:
    blocks = life_blocks(life_state("20:10"))
    assert "по распорядку (плана на сегодня нет)" in blocks.now
    assert "Ты сейчас: созвон с Соней (дома; с Соня), 20:00–20:30." in blocks.now
    assert "Занятость: занята" in blocks.now
    assert blocks.arcs == "" and blocks.canon == ""
    assert set(blocks.snapshot()) == {"routine", "now", "arcs", "canon"}


def test_blocks_without_any_life() -> None:
    blocks = life_blocks(life_state("19:30", who=persona(life={})))
    assert blocks.routine.startswith(f"{ROUTINE_HEADING}\n(распорядок не задан")
    assert "свободное время" in blocks.now and "На тебе" not in blocks.now
