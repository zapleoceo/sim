"""Russian call opening is explicit even for foreign metadata; no live provider calls."""
from __future__ import annotations

from sim.services.call_prompt import CALL_RULES, call_system_prompt, greeting
from sim.services.calls import language_code
from sim.services.prompt import PersonaProfile, StrategyProfile, Turn
from sim.services.prompt_map import fixed_rules
from tests.test_calls import ctx


def test_russian_opening_is_explicit_and_not_translated():
    first = greeting("Марина")
    for rule in ("ровно одно слово: «Алло»", "по-русски, не переводи",
                 "первая реплика остаётся русской", "Затем дождись ответа"):
        assert rule in first
    assert first == greeting("Maria")


def test_current_speech_not_foreign_phone_or_history_selects_language():
    prompt = call_system_prompt(
        PersonaProfile(name="Марина", language="ru", description="Ты Марина."),
        StrategyProfile(name="S"),
        [Turn("user", "Olá, meu número é +351 000 000 000")], ctx(),
    )
    for rule in ("начинай каждый звонок по-русски", "языка прежней переписки",
                 "После его речи отвечай на языке, на котором он сейчас говорит",
                 "на языке, который он явно попросил", "русский и украинский основные",
                 "Не определяй язык по номеру телефона", "недостаточно для смены языка"):
        assert rule in prompt
    assert "Olá" in prompt  # history is preserved as data, not erased to force language
    assert CALL_RULES in next(r.text for r in fixed_rules("Марина") if r.key == "call")


def test_actual_live_language_description_is_russian_not_autodetect():
    assert language_code("русский (переходит на украинский, если клиент пишет по-украински)") == "ru-RU"
