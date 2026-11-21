"""Pure parts of the day memory (docs/memory.md): her local day as a UTC interval (midnight edges,
daylight-saving days) and the Russian relative dates of «А помнишь…?»."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from sim.domain.plan import RecallSpec, ReplyPlan
from sim.domain.recall_dates import DateRange, resolve_phrase, resolve_range
from sim.services.context.builder import window_start
from sim.services.context.window import day_bounds, days_between, local_day, week_monday

KYIV = "Europe/Kyiv"
TODAY = date(2026, 11, 21)              # Saturday


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


# --- her local day -----------------------------------------------------------------------------


@pytest.mark.parametrize(("day", "start", "hours"), [
    (date(2026, 11, 21), utc(2026, 11, 20, 22), 24),
    (date(2026, 3, 29), utc(2026, 3, 28, 22), 23),          # clocks go forward at 03:00
    (date(2026, 10, 25), utc(2026, 10, 24, 21), 25),        # clocks go back at 04:00
])
def test_day_bounds_follow_her_time_zone(day: date, start: datetime, hours: int) -> None:
    first, end = day_bounds(day, KYIV)
    assert first == start and end - first == timedelta(hours=hours)
    assert day_bounds(day + timedelta(days=1), KYIV)[0] == end


def test_midnight_edges() -> None:
    assert local_day(utc(2026, 11, 20, 21, 59, 59), KYIV) == date(2026, 11, 20)
    assert local_day(utc(2026, 11, 20, 22, 0), KYIV) == date(2026, 11, 21)
    assert local_day(utc(2026, 11, 21, 4, 59), "America/New_York") == date(2026, 11, 20)
    assert local_day(utc(2026, 11, 21, 5, 0), "America/New_York") == date(2026, 11, 21)
    assert day_bounds(TODAY, "Not/AZone") == day_bounds(TODAY, KYIV)       # the default zone
    assert week_monday(date(2026, 11, 22)) == week_monday(TODAY) == date(2026, 11, 16)
    assert days_between(date(2026, 11, 20), TODAY) == [date(2026, 11, 20), TODAY]
    assert days_between(TODAY, date(2026, 11, 20)) == []


def test_window_start() -> None:
    now = utc(2026, 11, 21, 18, 30)
    assert window_start(now, KYIV, yesterday_ready=True,
                        unanswered_at=None) == utc(2026, 11, 20, 20)    # 22:00 her time
    assert window_start(now, KYIV, yesterday_ready=False,
                        unanswered_at=None) == utc(2026, 11, 19, 22)    # all of yesterday
    old = utc(2026, 11, 19, 10)
    assert window_start(now, KYIV, yesterday_ready=True, unanswered_at=old) == old
    assert window_start(now, KYIV, yesterday_ready=True,
                        unanswered_at=utc(2026, 11, 1)) == now - timedelta(days=3)


# --- relative dates ----------------------------------------------------------------------------


def one(day: date) -> DateRange:
    return DateRange(day, day)


@pytest.mark.parametrize(("phrase", "expected"), [
    ("сегодня", one(TODAY)),
    ("Вчера вечером", one(date(2026, 11, 20))),
    ("позавчера", one(date(2026, 11, 19))),
    ("позапозавчера", one(date(2026, 11, 18))),
    ("2026-11-15", one(date(2026, 11, 15))),
    ("15.11", one(date(2026, 11, 15))),
    ("25.12", one(date(2025, 12, 25))),                     # still ahead: last year
    ("15.11.26", one(date(2026, 11, 15))),
    ("20 ноября", one(date(2026, 11, 20))),
    ("25 декабря", one(date(2025, 12, 25))),
    ("20-го", one(date(2026, 11, 20))),
    ("25-го числа", one(date(2026, 10, 25))),               # this month's 25th is ahead
    ("3 дня назад", one(date(2026, 11, 18))),
    ("три дня назад", one(date(2026, 11, 18))),
    ("день назад", one(date(2026, 11, 20))),
    ("пару дней назад", one(date(2026, 11, 19))),
    ("несколько дней назад", DateRange(date(2026, 11, 16), date(2026, 11, 18))),
    ("неделю назад", DateRange(date(2026, 11, 13), date(2026, 11, 15))),
    ("две недели назад", DateRange(date(2026, 11, 6), date(2026, 11, 8))),
    ("месяц назад", DateRange(date(2026, 10, 19), date(2026, 10, 25))),
    ("в пятницу", one(date(2026, 11, 20))),
    ("во вторник", one(date(2026, 11, 17))),
    ("в воскресенье", one(date(2026, 11, 15))),
    ("в субботу", one(date(2026, 11, 14))),                 # today is Saturday: the last one
    ("в прошлую субботу", one(date(2026, 11, 14))),
    ("в позапрошлую субботу", one(date(2026, 11, 7))),
    ("в эту среду", one(date(2026, 11, 18))),
    ("на прошлой неделе", DateRange(date(2026, 11, 9), date(2026, 11, 15))),
    ("на этой неделе", DateRange(date(2026, 11, 16), TODAY)),
    ("на позапрошлой неделе", DateRange(date(2026, 11, 2), date(2026, 11, 8))),
    ("в прошлом месяце", DateRange(date(2026, 10, 1), date(2026, 10, 31))),
    ("в этом месяце", DateRange(date(2026, 11, 1), TODAY)),
    ("на выходных", DateRange(date(2026, 11, 14), date(2026, 11, 15))),
])
def test_phrases_resolve_against_her_today(phrase: str, expected: DateRange) -> None:
    assert resolve_phrase(phrase, TODAY) == expected


@pytest.mark.parametrize("phrase", ["", "когда-то", "давно", "в прошлом году", "31.02",
                                    "в эту пятницу будущую"])
def test_unknown_phrases_resolve_to_nothing(phrase: str) -> None:
    assert resolve_phrase(phrase, TODAY) is None


def test_ranges_are_ordered_bounded_and_never_in_the_future() -> None:
    assert resolve_range("позавчера", "вчера", TODAY) == DateRange(date(2026, 11, 19),
                                                                   date(2026, 11, 20))
    assert resolve_range("вчера", "", TODAY) == one(date(2026, 11, 20))
    assert resolve_range("вчера", "позавчера", TODAY) == DateRange(date(2026, 11, 19),
                                                                   date(2026, 11, 20))
    assert resolve_range("2026-12-01", "", TODAY) is None
    assert resolve_range("не знаю", "", TODAY) is None
    long = resolve_range("2026-01-01", "вчера", TODAY)
    assert long is not None and long.days == 31 and long.last == date(2026, 11, 20)


def test_the_plan_asks_to_recall_with_dates_or_a_topic() -> None:
    plan = ReplyPlan.model_validate({"messages": [], "recall": {"date_from": "позавчера"}})
    assert plan.wants_recall
    assert ReplyPlan.model_validate({"recall": {"query": "работа"}}).wants_recall
    answered = ReplyPlan.model_validate({"messages": [{"type": "text", "text": "да"}],
                                         "recall": {"query": "работа"}})
    assert not answered.wants_recall                    # with messages the recall is ignored
    with pytest.raises(ValueError, match="date_from or query"):
        RecallSpec.model_validate({"date_to": "вчера"})
    assert "recall" in ReplyPlan.model_json_schema()["properties"]
