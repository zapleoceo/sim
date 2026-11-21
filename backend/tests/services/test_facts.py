from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from sim.services.capabilities import Capabilities
from sim.services.context.facts import (
    FactsInput,
    age_on,
    clock_duration,
    compute_facts,
    human_gap,
    part_of_day,
    plural_ru,
    stamp,
    to_local,
    years_word,
)

NOW = datetime(2026, 11, 21, 18, 30, tzinfo=UTC)     # Saturday, 20:30 in Kyiv (UTC+2)


@pytest.mark.parametrize(("hour", "part"), [
    (0, "ночь"), (4, "ночь"), (5, "утро"), (11, "утро"), (12, "день"), (16, "день"),
    (17, "вечер"), (22, "вечер"), (23, "ночь"),
])
def test_part_of_day(hour: int, part: str) -> None:
    assert part_of_day(hour) == part


def test_age_changes_on_the_birthday() -> None:
    birth = date(2001, 11, 22)
    assert age_on(birth, date(2026, 11, 21)) == 24
    assert age_on(birth, date(2026, 11, 22)) == 25


@pytest.mark.parametrize(("n", "word"), [
    (1, "год"), (2, "года"), (4, "года"), (5, "лет"), (11, "лет"), (12, "лет"), (21, "год"),
    (22, "года"), (25, "лет"), (111, "лет"),
])
def test_years_word(n: int, word: str) -> None:
    assert years_word(n) == word
    assert plural_ru(n, "год", "года", "лет") == word


@pytest.mark.parametrize(("delta", "text"), [
    (timedelta(seconds=20), "меньше минуты"),
    (timedelta(minutes=3), "3 мин"),
    (timedelta(hours=2), "2 ч"),
    (timedelta(hours=2, minutes=5), "2 ч 5 мин"),
    (timedelta(days=3), "3 дн."),
    (timedelta(days=1, hours=4), "1 дн. 4 ч"),
    (timedelta(seconds=-5), "меньше минуты"),
])
def test_human_gap(delta: timedelta, text: str) -> None:
    assert human_gap(delta) == text


def test_local_time_and_stamp_use_the_zone() -> None:
    assert to_local(NOW, "Europe/Kyiv").hour == 20
    assert to_local(NOW.replace(tzinfo=None), "Europe/Kyiv").hour == 20   # naive = UTC
    assert to_local(NOW, "Nowhere/Bad").hour == 20                        # falls back to Kyiv
    assert stamp(NOW, "Europe/Kyiv") == "21.11 20:30"
    assert clock_duration(192) == "3:12"
    assert clock_duration(None) == "0:00"


def test_compute_facts_lines() -> None:
    facts = compute_facts(FactsInput(
        now=NOW, persona_tz="Europe/Kyiv", birth_date=date(2001, 3, 3), client_tz="Europe/Warsaw",
        first_contact_at=NOW - timedelta(days=9), last_client_at=NOW - timedelta(minutes=3),
        last_persona_at=NOW - timedelta(hours=2, minutes=5), messages_count=54, calls_count=2,
        active_call=True, generations=("видео «утро у окна»",),
        capabilities=Capabilities(voice=True, photo=True, photo_generate=False, video=True,
                                  video_generate=False),
    ))
    text = facts.text
    assert "Сейчас у тебя: суббота, 21 ноября 2026, 20:30 (вечер), часовой пояс Europe/Kyiv." \
        in text
    assert "Вчера было: пятница, завтра будет: воскресенье." in text
    assert "Тебе 25 лет (родилась 3 марта 2001)." in text
    assert "У собеседника сейчас 19:30, суббота (Europe/Warsaw)." in text
    assert "Последнее сообщение собеседника было 3 мин назад." in text
    assert "Твоё последнее сообщение было 2 ч 5 мин назад." in text
    assert "Переписка началась 12 ноября 2026 (9 дн. назад)." in text
    assert "Всего сообщений в чате: 54, звонков: 2." in text
    assert "Сейчас идёт звонок" in text
    assert "видео «утро у окна»" in text
    assert "голосовые — да; фото из каталога — да; новые фото — нет" in text
    assert facts.values["age"] == 25
    assert facts.values["part_of_day"] == "вечер"
    assert facts.values["since_client_s"] == 180


def test_compute_facts_birthday_and_empty_chat() -> None:
    facts = compute_facts(FactsInput(now=NOW, persona_tz="Europe/Kyiv",
                                     birth_date=date(2001, 11, 21)))
    assert "Тебе 25 лет (родилась 21 ноября 2001), сегодня твой день рождения." in facts.text
    assert "Собеседник ещё ничего не писал." in facts.text
    assert "Твоих сообщений в этом чате ещё не было." in facts.text
    assert "age" in facts.values


def test_compute_facts_is_deterministic() -> None:
    inp = FactsInput(now=NOW, persona_tz="Europe/Kyiv", last_client_at=NOW - timedelta(hours=1))
    assert compute_facts(inp) == compute_facts(inp)
