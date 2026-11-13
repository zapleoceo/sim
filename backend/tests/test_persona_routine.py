"""The persona's routine: parsed once, where she is now / was / will be computed by code."""
from __future__ import annotations

from datetime import date, datetime

from alembic import command
from sqlalchemy import select

from sim.adapters.db.models import Persona
from sim.adapters.db.session import transaction
from sim.services.persona_clock import persona_facts
from sim.services.persona_routine import (
    ROUTINE_HEADER,
    agenda,
    invalid_lines,
    parse_line,
    parse_routine,
    routine_block,
    routine_facts,
)
from sim.services.prompt import PersonaProfile, StrategyProfile, stable_system_prompt
from tests.test_conversation import run_reply, start
from tests.test_migrations import alembic_config, columns

ROUTINE = """
# Марина, обычная неделя
пн-пт 06:30-07:15 пробежка вдоль Эльбы
пн-пт 09:00-13:00 консультации с клиентами, из дома
сб,вс 09:00-10:30 долгий завтрак с Андреем
вс 13:00-16:00 обед у мамы
ежедневно 23:00-06:30 сплю
2026-11-16 09:00-11:00 стоматолог на Пражской
"""
FRIDAY = date(2026, 11, 13)


def labels(day: date) -> list[str]:
    return [s.label() for s in agenda(parse_routine(ROUTINE), day)]


def test_lines_parse_days_ranges_lists_and_dates():
    assert parse_line("пн-пт 09:00-13:00 работа").days == frozenset(range(5))
    assert parse_line("сб,вс 9:00-10:30 завтрак").days == frozenset({5, 6})
    assert parse_line("пт-пн 20:00-21:00 кино").days == frozenset({4, 5, 6, 0})
    assert parse_line("каждый день 23:00-06:30 сплю").days == frozenset(range(7))
    assert parse_line("2026-11-16 09:00-11:00 врач").on == date(2026, 11, 16)
    for bad in ("пн 9-13 работа", "понедельник 09:00-13:00 работа", "пн 25:00-26:00 x",
                "пн 09:00-09:00 ничего", "2026-02-30 09:00-10:00 x", "пн 09:00-13:00"):
        assert parse_line(bad) is None, bad


def test_invalid_lines_skip_comments_and_blanks():
    assert invalid_lines(ROUTINE) == []
    assert invalid_lines("пн 09:00-13:00 работа\nвсегда 10:00-11:00 x\n# заметка") == ["всегда 10:00-11:00 x"]


def test_a_day_holds_its_entries_and_last_night_tail():
    assert labels(FRIDAY) == [
        "00:00-06:30 сплю", "06:30-07:15 пробежка вдоль Эльбы",
        "09:00-13:00 консультации с клиентами, из дома", "23:00-06:30 сплю",
    ]


def test_a_dated_one_off_replaces_the_weekly_entry_it_overlaps():
    monday = labels(date(2026, 11, 16))
    assert "09:00-11:00 стоматолог на Пражской" in monday
    assert "09:00-13:00 консультации с клиентами, из дома" not in monday
    assert "06:30-07:15 пробежка вдоль Эльбы" in monday


def test_facts_say_where_she_is_was_and_will_be():
    lines = routine_facts(ROUTINE, datetime(2026, 11, 13, 10, 15))
    assert lines[0] == "- Где ты сейчас по распорядку: консультации с клиентами, из дома (с 09:00 до 13:00)"
    assert lines[1] == "- Сегодня уже было: 00:00-06:30 сплю; 06:30-07:15 пробежка вдоль Эльбы"
    assert lines[2] == "- Дальше сегодня: 23:00-06:30 сплю"
    assert lines[3].startswith("- Вчера по распорядку: 00:00-06:30 сплю; 06:30-07:15 пробежка")
    assert lines[4] == "- Завтра по распорядку: 00:00-06:30 сплю; 09:00-10:30 долгий завтрак с Андреем; 23:00-06:30 сплю"


def test_free_time_and_an_empty_routine():
    assert routine_facts(ROUTINE, datetime(2026, 11, 13, 15, 0))[0] == \
        "- Где ты сейчас по распорядку: по распорядку ничего, свободное время"
    assert routine_facts("", datetime(2026, 11, 13, 15, 0)) == []
    assert routine_facts(ROUTINE, datetime(2026, 11, 14, 0, 30))[0].startswith("- Где ты сейчас по распорядку: сплю")


def test_the_same_moment_always_gives_the_same_answer():
    moment = datetime(2026, 11, 15, 14, 0)
    assert routine_facts(ROUTINE, moment) == routine_facts(ROUTINE, moment)
    assert "обед у мамы (с 13:00 до 16:00)" in routine_facts(ROUTINE, moment)[0]


def test_persona_facts_use_her_local_time_for_the_routine():
    # 08:15 UTC is 10:15 in Kyiv (UTC+2 in November)
    text = persona_facts(None, "Europe/Kyiv", datetime(2026, 11, 13, 8, 15), ROUTINE)
    assert "Где ты сейчас по распорядку: консультации с клиентами" in text


def test_the_stable_prompt_carries_the_weekly_routine_after_her_text():
    prompt = stable_system_prompt(PersonaProfile(name="Марина", description="Я Марина.", routine=ROUTINE),
                                  StrategyProfile())
    assert prompt.startswith("Я Марина.\n\n" + ROUTINE_HEADER)
    assert "вс 13:00-16:00 обед у мамы" in prompt and "# Марина" not in prompt
    assert ROUTINE_HEADER not in stable_system_prompt(PersonaProfile(name="М", description="d"), StrategyProfile())
    assert routine_block("") == ""


async def test_the_reply_prompt_carries_the_routine(container, world, fake_llm):
    async with transaction(container.factory) as s:
        (await s.scalars(select(Persona))).first().routine = ROUTINE
    await start(container, world)
    await run_reply(container)
    prompt = "\n".join(m["content"] for m in fake_llm.gen_calls[-1][0])
    assert ROUTINE_HEADER in prompt and "Где ты сейчас по распорядку:" in prompt


async def test_persona_api_round_trips_the_routine_and_rejects_bad_lines(api):
    from tests.test_api_smoke import owner

    h = await owner(api)
    created = (await api.post("/api/personas", json={"name": "Марина"}, headers=h)).json()
    assert created["routine"] == ""
    upd = (await api.put(f"/api/personas/{created['id']}", json={"name": "Марина", "routine": ROUTINE},
                         headers=h)).json()
    assert upd["routine"] == ROUTINE.strip()       # bodies are stripped
    for bad in ("понедельник с утра бегаю", "пн 9-13 работа", "x" * 4001):
        resp = await api.put(f"/api/personas/{created['id']}", json={"name": "Марина", "routine": bad}, headers=h)
        assert resp.status_code == 422, bad


def test_0019_adds_routine_and_roundtrips(tmp_path):
    cfg = alembic_config(tmp_path / "mig.db")
    command.upgrade(cfg, "head")
    assert "routine" in columns(tmp_path / "mig.db", "persona")
    command.downgrade(cfg, "0018")
    assert "routine" not in columns(tmp_path / "mig.db", "persona")
