"""How she writes right now and what her phone does to the text (docs/writing.md)."""
from __future__ import annotations

import random
from datetime import date, datetime

from sim.domain.life import Ambience, BusyLevel
from sim.domain.scene import Light, SceneCard
from sim.domain.writing import DEFAULT_MODE, Device, Pace, mode
from sim.services.writing import (
    NO_REFUSALS,
    STYLE_HEADING,
    add_typo,
    humanize,
    message_rng,
    plain_marks,
    style_block,
    writing_mode,
)

EVENING = datetime(2026, 11, 21, 20, 20)
NIGHT = datetime(2026, 11, 21, 23, 40)


def scene(activity: str, *, place: str = "дома",
          busy: BusyLevel = BusyLevel.FREE) -> SceneCard:
    return SceneCard(day=date(2026, 11, 21), event_id="e", place=place, place_details="",
                     activity=activity, with_whom=(), outfit="свитер", light=Light.EVENING_LAMP,
                     ambience=Ambience.QUIET_HOME, busy=busy, indoors=True)


def test_the_mode_follows_what_she_is_doing() -> None:
    # 2026-11-21: on a call with her daughter she wrote three long explanations.
    call = writing_mode(scene("созвон с Соней", busy=BusyLevel.PARTIAL), EVENING)
    assert (call.device, call.pace, call.max_messages) == (Device.PHONE, Pace.BUSY, 1)
    tram = writing_mode(scene("едет домой", place="трамвай 13"), EVENING)
    assert (tram.pace, tram.max_chars) == (Pace.MOVING, 60)
    assert writing_mode(scene("поднимается в лифте"), EVENING).pace == Pace.MOVING
    work = writing_mode(scene("работает за ноутбуком над отчётом"), EVENING)
    assert (work.device, work.pace) == (Device.LAPTOP, Pace.RELAXED)
    assert writing_mode(scene("готовит ужин"), EVENING).pace == Pace.BUSY
    assert writing_mode(scene("чтение Канемана"), EVENING) == mode(
        Device.PHONE, Pace.RELAXED, "чтение Канемана")
    assert writing_mode(scene("читает в кровати"), NIGHT).pace == Pace.SLEEPY
    assert writing_mode(None, EVENING) == DEFAULT_MODE
    assert writing_mode(None, NIGHT).pace == Pace.SLEEPY


def test_the_style_block_has_limits_and_the_anti_slop_rules() -> None:
    block = style_block(writing_mode(scene("едет в трамвае"), EVENING))
    assert block.startswith(f"{STYLE_HEADING}\nСейчас ты пишешь с телефона: едет в трамвае.")
    assert "одной рукой" in block and "не больше 1 сообщ., каждое до ~60 знаков" in block
    assert "Не начинай сообщения с имени собеседника" in block
    assert "короткий дефис" in block and "не просто…, а…" in block
    assert NO_REFUSALS not in block
    assert style_block(DEFAULT_MODE, no_refusals=True).endswith(NO_REFUSALS)


def test_plain_marks_are_what_a_phone_types() -> None:
    assert plain_marks("Спальня — это личное… «румтур» – нет") == (
        'Спальня - это личное... "румтур" - нет')
    assert plain_marks("**Ок**  ну  да") == "Ок ну да"
    assert plain_marks("кто-то из-за") == "кто-то из-за"         # hyphens stay


def test_the_phone_drops_the_full_stop_and_sometimes_the_capital() -> None:
    calm = mode(Device.PHONE, Pace.RELAXED, "дома")
    never = random.Random(1)
    never.random = lambda: 0.99                                  # type: ignore[method-assign]
    assert humanize("Давай позже.", calm, never) == "Давай позже"
    assert humanize("Ну как так?", calm, never) == "Ну как так?"
    always = random.Random(1)
    always.random = lambda: 0.0                                  # type: ignore[method-assign]
    no_typos = mode(Device.PHONE, Pace.RELAXED, "дома")
    lowered = humanize("Давай", no_typos, always)
    assert lowered.lower().startswith("д") and lowered[0] == "д"
    assert humanize("ОК, давай", no_typos, always).startswith("ОК")  # an abbreviation stays
    laptop = mode(Device.LAPTOP, Pace.RELAXED, "работа")
    assert humanize("Давай позже.", laptop, never) == "Давай позже."


def test_a_typo_is_one_typical_smartphone_slip() -> None:
    text = "Андрей уже приехал, сейчас ужинаем"
    for seed in range(40):
        typo = add_typo(text, random.Random(seed))
        changed = [(a, b) for a, b in zip(text.split(), typo.split(), strict=True) if a != b]
        assert len(changed) == 1, (seed, typo)
        before, after = changed[0]
        assert before[0] == after[0] and before != "Андрей"        # names are not touched
        assert abs(len(before) - len(after)) <= 1
    assert add_typo("ок, да", random.Random(1)) == "ок, да"         # no word to slip on


def test_typos_are_rarer_on_the_laptop_and_stable_for_a_message() -> None:
    text = "сегодня оказалось совсем непросто"
    phone = mode(Device.PHONE, Pace.MOVING, "на ходу")
    laptop = mode(Device.LAPTOP, Pace.RELAXED, "работа")
    hurried = sum(humanize(text, phone, message_rng(run, 0)) != text for run in range(200))
    calm = sum(humanize(text, laptop, message_rng(run, 0)) != text for run in range(200))
    assert hurried > 40 and calm < 20
    assert humanize(text, phone, message_rng(7, 1)) == humanize(text, phone, message_rng(7, 1))
