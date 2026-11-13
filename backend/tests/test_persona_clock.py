"""Persona facts computed by code (age, zodiac, local time) and messenger touches (typos, case)."""
from __future__ import annotations

from datetime import date, datetime

import pytest
from alembic import command
from sqlalchemy import select

from sim.adapters.db.models import Persona
from sim.adapters.db.session import transaction
from sim.services.humanize import TYPO_WORD, messenger_touches
from sim.services.persona_clock import age_on, days_to_birthday, persona_facts, zodiac
from tests.test_conversation import run_reply, start
from tests.test_migrations import alembic_config, columns


@pytest.mark.parametrize("d,sign", [
    (date(1976, 12, 9), "Стрелец"), (date(1980, 12, 22), "Козерог"), (date(1980, 1, 19), "Козерог"),
    (date(1980, 1, 20), "Водолей"), (date(1980, 3, 20), "Рыбы"), (date(1980, 3, 21), "Овен"),
    (date(1980, 7, 23), "Лев"), (date(1980, 11, 21), "Скорпион"), (date(1980, 11, 22), "Стрелец"),
])
def test_zodiac_bounds(d, sign):
    assert zodiac(d) == sign


def test_age_and_days_to_birthday():
    birth = date(1976, 12, 9)
    assert age_on(birth, date(2026, 12, 8)) == 49 and age_on(birth, date(2026, 12, 9)) == 50
    assert days_to_birthday(birth, date(2026, 11, 13)) == 26
    assert days_to_birthday(birth, date(2026, 12, 10)) == 364
    assert days_to_birthday(date(1980, 2, 29), date(2027, 2, 28)) == 1  # 1 March in a common year


def test_facts_use_the_local_time_zone():
    text = persona_facts(date(1976, 12, 9), "Europe/Kyiv", datetime(2026, 11, 13, 22, 30))
    # 22:30 UTC is 00:30 next day in Kyiv (UTC+2 in November)
    assert "суббота, 14 ноября 2026, 00:30 (ночь)" in text
    assert "Тебе 49 (родилась 9 декабря 1976), знак зодиака Стрелец, до дня рождения 25 дн." in text


def test_facts_without_a_birth_date_and_with_a_bad_zone():
    text = persona_facts(None, "Mars/Base", datetime(2026, 11, 13, 10, 0))
    assert "12:00 (день)" in text and "Тебе" not in text


async def test_the_persona_prompt_carries_the_facts(container, world, fake_llm):
    async with transaction(container.factory) as s:
        (await s.scalars(select(Persona))).first().birth_date = date(1976, 12, 9)
    await start(container, world)
    await run_reply(container)
    prompt = "\n".join(m["content"] for m in fake_llm.gen_calls[-1][0])
    assert "ТВОИ ТОЧНЫЕ ДАННЫЕ НА СЕЙЧАС" in prompt and "знак зодиака Стрелец" in prompt


def test_touches_drop_the_final_full_stop_and_are_deterministic():
    text = "Привет. Я сегодня бегала по набережной."
    once = messenger_touches(text, typo_percent=0, lowercase_percent=0)
    assert once == "Привет. Я сегодня бегала по набережной"
    assert messenger_touches(text, typo_percent=100, lowercase_percent=100) == \
        messenger_touches(text, typo_percent=100, lowercase_percent=100)
    assert messenger_touches("Ну да...", typo_percent=0, lowercase_percent=0) == "Ну да..."


def test_touches_lowercase_the_start_and_make_one_typo():
    text = "Слушай, расскажи подробнее про работу"
    out = messenger_touches(text, typo_percent=100, lowercase_percent=100)
    assert out[0] == "с" and out != "слушай, расскажи подробнее про работу"
    assert sum(a != b for a, b in zip(out, "слушай, расскажи подробнее про работу")) >= 1


@pytest.mark.parametrize("text", [
    "Вот ссылка https://rcipm.example/регистрация?ref=ABC123",
    "Стоимость 12500 грн, Марина",
    "Андрей Максим Соня",
])
def test_typos_never_touch_links_numbers_or_names(text):
    for seed_text in (text, text + " ок"):
        out = messenger_touches(seed_text, typo_percent=100, lowercase_percent=0)
        for token in ("https://rcipm.example/регистрация?ref=ABC123", "12500", "Марина", "Андрей", "Соня"):
            if token in seed_text:
                assert token in out


def test_typo_word_skips_short_and_capitalised_words():
    assert [m.group(0) for m in TYPO_WORD.finditer("Марина и кот спят дома, наверное")] == ["наверное"]


def test_0018_adds_birth_date_and_timezone_and_roundtrips(tmp_path):
    db = tmp_path / "mig.db"
    cfg = alembic_config(db)
    command.upgrade(cfg, "head")
    assert {"birth_date", "timezone"} <= columns(db, "persona")
    command.downgrade(cfg, "0017")
    assert not {"birth_date", "timezone"} & columns(db, "persona")
