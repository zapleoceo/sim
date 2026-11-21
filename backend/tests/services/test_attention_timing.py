"""When she picks up her phone (sim/services/attention/timing.py, docs/attention.md): the
log-normal delays per busy level and pace, her availability from the seed routine after the
owner's settings (modes, «не беспокоить», the chat's «Отвечать сразу»), the fallback time and
«HH:MM» of the agent. Pure: fixed or seeded uniform numbers, no database."""
from __future__ import annotations

from datetime import date, timedelta
from statistics import median

import pytest

from sim.domain.attention import AttentionMode, AttentionSettings
from sim.domain.life import BusyLevel
from sim.services.attention.timing import (
    FREE_SPREAD,
    PARTIAL_SPREAD,
    Availability,
    availability,
    check_delay,
    fallback_at,
    later_at,
    local_hhmm,
    lognormal,
)
from sim.services.life.stable import stable_fraction
from tests.services.life_seed import SATURDAY, TZ, life_state, utc

SUNDAY = SATURDAY + timedelta(days=1)
HUMAN = AttentionSettings()


def seeded(n: int, seed: str) -> list[float]:
    """`n` reproducible uniform numbers."""
    return [stable_fraction(f"{seed}:{i}") for i in range(n)]


def test_lognormal_median_and_bounds() -> None:
    assert lognormal(FREE_SPREAD, 0.5) == pytest.approx(20.0)
    assert lognormal(PARTIAL_SPREAD, 0.5) == pytest.approx(480.0)
    assert lognormal(FREE_SPREAD, 0.0) == FREE_SPREAD.low_s
    assert lognormal(FREE_SPREAD, 1.0) == FREE_SPREAD.high_s
    assert lognormal(PARTIAL_SPREAD, 0.001) == PARTIAL_SPREAD.low_s
    assert lognormal(FREE_SPREAD, 0.3) < lognormal(FREE_SPREAD, 0.7)


@pytest.mark.parametrize(("spread", "low", "high", "mid"), [
    (FREE_SPREAD, 3.0, 120.0, (12.0, 30.0)),            # seconds to ~2 min, median ~20 s
    (PARTIAL_SPREAD, 180.0, 1200.0, (360.0, 600.0)),    # 3-20 min, median ~8 min
])
def test_seeded_samples_stay_in_their_range(spread, low, high, mid) -> None:
    samples = [lognormal(spread, u) for u in seeded(400, "attention")]
    assert all(low <= s <= high for s in samples)
    assert mid[0] <= median(samples) <= mid[1]
    assert samples == [lognormal(spread, u) for u in seeded(400, "attention")]   # stable


@pytest.mark.parametrize(("hhmm", "day", "level", "activity", "ends", "sleep"), [
    ("19:30", SATURDAY, BusyLevel.FREE, "свободное время", None, False),
    ("20:10", SATURDAY, BusyLevel.PARTIAL, "созвон с Соней", "20:30", False),
    ("07:00", SATURDAY, BusyLevel.AWAY, "пробежка 5 км", "07:30", False),
    ("02:00", SUNDAY, BusyLevel.AWAY, "сон, телефон в другой комнате", "06:30", True),
])
def test_availability_from_the_routine(hhmm: str, day: date, level: BusyLevel, activity: str,
                                       ends: str | None, sleep: bool) -> None:
    av = availability(life_state(hhmm, day=day), HUMAN)
    assert (av.level, av.activity, av.sleep) == (level, activity, sleep)
    assert (local_hhmm(av.ends_at, TZ) if av.ends_at else None) == ends


@pytest.mark.parametrize(("settings", "chat_instant", "hhmm", "day", "level"), [
    (AttentionSettings(mode=AttentionMode.FREE_ONLY), False, "20:10", SATURDAY,
     BusyLevel.FREE),                                   # busy counts as free
    (AttentionSettings(mode=AttentionMode.FREE_ONLY), False, "02:00", SUNDAY,
     BusyLevel.AWAY),                                   # away is kept
    (AttentionSettings(mode=AttentionMode.INSTANT), False, "02:00", SUNDAY, BusyLevel.FREE),
    (HUMAN, True, "02:00", SUNDAY, BusyLevel.FREE),     # the chat's «Отвечать сразу»
    (AttentionSettings(night_dnd=False), False, "02:00", SUNDAY,
     BusyLevel.PARTIAL),                                # phone on at night: a glance
    (AttentionSettings(night_dnd=False), False, "07:00", SATURDAY,
     BusyLevel.AWAY),                                   # a run is not sleep
])
def test_settings_change_the_level(settings: AttentionSettings, chat_instant: bool, hhmm: str,
                                   day: date, level: BusyLevel) -> None:
    av = availability(life_state(hhmm, day=day), settings, instant=chat_instant)
    assert av.level == level


@pytest.mark.parametrize(("pace", "expected"), [(0.5, 10.0), (1.0, 20.0), (2.0, 40.0)])
def test_free_check_is_a_glance_times_the_pace(pace: float, expected: float) -> None:
    now = utc(SATURDAY, "19:30")
    av = availability(life_state("19:30"), HUMAN)
    assert check_delay(av, now, pace, 0.5) == pytest.approx(expected)


def test_busy_check_is_minutes_but_not_after_the_event() -> None:
    now = utc(SATURDAY, "20:10")
    av = availability(life_state("20:10"), HUMAN)
    assert check_delay(av, now, 1.0, 0.5) == pytest.approx(480.0)
    assert check_delay(av, now, 2.0, 0.5) == pytest.approx(960.0)
    assert check_delay(av, now, 2.0, 0.99) == pytest.approx(1200.0)     # the call ends 20:30
    late = utc(SATURDAY, "20:28")
    assert check_delay(availability(life_state("20:28"), HUMAN), late, 1.0, 0.5) == \
        pytest.approx(120.0)


def test_away_check_waits_for_the_end_of_the_event() -> None:
    now = utc(SUNDAY, "02:00")
    av = availability(life_state("02:00", day=SUNDAY), HUMAN)
    assert check_delay(av, now, 1.0, 0.5) == pytest.approx(4.5 * 3600 + 20.0)
    assert check_delay(av, now, 2.0, 0.5) == pytest.approx(4.5 * 3600 + 40.0)


def test_fallback_is_the_end_of_the_busy_event_or_a_glance() -> None:
    busy_now = utc(SATURDAY, "20:10")
    busy = availability(life_state("20:10"), HUMAN)
    assert fallback_at(busy, busy_now, 1.0, 0.5) == utc(SATURDAY, "20:30")
    free_now = utc(SATURDAY, "19:30")
    free = Availability(level=BusyLevel.FREE)
    assert fallback_at(free, free_now, 1.0, 0.5) == free_now + timedelta(seconds=20)


@pytest.mark.parametrize(("hhmm", "expected"), [
    ("20:15", utc(SATURDAY, "20:15")),                  # later today
    ("19:00", utc(SATURDAY, "19:30")),                  # half an hour ago: as soon as possible
    ("06:00", utc(SUNDAY, "06:00")),                    # long ago: tomorrow morning
])
def test_later_is_her_next_such_moment(hhmm: str, expected) -> None:
    assert later_at(hhmm, utc(SATURDAY, "19:30"), TZ) == expected
