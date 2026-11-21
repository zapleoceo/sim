"""When she picks up her phone (docs/attention.md, «Когда она берёт телефон»). Code decides from
her scene, no model, instantly:

- away (sleep, a run, driving; at night with «не беспокоить» the routine's sleep) — no look
  until the event ends, then a short glance delay;
- partial (busy) — 3–20 minutes (log-normal, median 8 min), but not after the event ends;
- free — seconds to ~2 minutes (log-normal, median ~20 s).

Every sampled delay is multiplied by the owner's pace (0.5–2). The randomness comes in as a
uniform number u in [0, 1) and is turned into a log-normal one by the inverse CDF, so tests
pass fixed numbers. Pure functions."""
from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from statistics import NormalDist

from sim.domain.attention import AttentionMode, AttentionSettings
from sim.domain.life import BusyLevel
from sim.services.context.facts import to_local
from sim.services.life.store import LifeState

Uniform = Callable[[], float]               # a number in [0, 1)

FREE_ACTIVITY = "свободное время"
# The routine's sleep slot, found by its words («сон, телефон в другой комнате»).
# Whole words only: «сон» must not match «Соней» (a call with Sonya at 20:00 is not sleep).
SLEEP_WORDS = re.compile(r"\b(сон|сна|сну|сплю|спит|спать|sleep)\b", re.IGNORECASE)
# A «later» time this far in the past is a slip of the model: it means «as soon as possible».
PAST_GRACE = timedelta(hours=6)
_NORMAL = NormalDist()
_EDGE = 1e-6


@dataclass(frozen=True)
class Spread:
    """A log-normal delay: its median, spread and hard bounds, in seconds."""

    median_s: float
    sigma: float
    low_s: float
    high_s: float


FREE_SPREAD = Spread(median_s=20.0, sigma=0.9, low_s=3.0, high_s=120.0)
PARTIAL_SPREAD = Spread(median_s=480.0, sigma=0.5, low_s=180.0, high_s=1200.0)


def lognormal(spread: Spread, u: float) -> float:
    """The delay at quantile `u` of the spread (u = 0.5 is the median), within its bounds."""
    u = min(max(u, _EDGE), 1.0 - _EDGE)
    value = spread.median_s * math.exp(spread.sigma * _NORMAL.inv_cdf(u))
    return min(max(value, spread.low_s), spread.high_s)


@dataclass(frozen=True)
class Availability:
    """How reachable she is now, after the owner's settings: the level that decides, what she
    does and when it ends (UTC; None = nothing planned)."""

    level: BusyLevel
    activity: str = FREE_ACTIVITY
    place: str = ""
    with_whom: tuple[str, ...] = ()
    ends_at: datetime | None = None
    sleep: bool = False


def effective_level(busy: BusyLevel, *, sleep: bool, settings: AttentionSettings,
                    instant: bool = False) -> BusyLevel:
    """«Всегда сразу» is always free; «не беспокоить» makes her sleep away (without it the
    phone is on and she may glance at it: partial); «Только когда свободна» treats busy as
    free and keeps away."""
    if settings.is_instant(instant):
        return BusyLevel.FREE
    level = busy
    if sleep:
        level = BusyLevel.AWAY if settings.night_dnd else BusyLevel.PARTIAL
    if settings.mode == AttentionMode.FREE_ONLY and level == BusyLevel.PARTIAL:
        return BusyLevel.FREE
    return level


def availability(life: LifeState, settings: AttentionSettings, *,
                 instant: bool = False) -> Availability:
    """Her availability now, from the agenda of her local day (plan, else routine)."""
    agenda = life.agenda()
    slot = agenda.at(agenda.minute_of(life.local))
    if slot is None:
        level = effective_level(BusyLevel.FREE, sleep=False, settings=settings, instant=instant)
        return Availability(level=level)
    sleep = bool(SLEEP_WORDS.search(slot.title))
    midnight = datetime.combine(agenda.day, time(), tzinfo=life.local.tzinfo)
    return Availability(
        level=effective_level(slot.busy, sleep=sleep, settings=settings, instant=instant),
        activity=slot.title, place=slot.place, with_whom=slot.with_whom,
        ends_at=(midnight + timedelta(minutes=slot.end)).astimezone(UTC), sleep=sleep)


def check_delay(av: Availability, now: datetime, pace: float, u: float) -> float:
    """Seconds from `now` until she looks at her phone."""
    glance = pace * lognormal(FREE_SPREAD, u)
    if av.level == BusyLevel.FREE:
        return glance
    left = max((av.ends_at - now).total_seconds(), 0.0) if av.ends_at is not None else None
    if av.level == BusyLevel.PARTIAL:
        sampled = pace * lognormal(PARTIAL_SPREAD, u)
        return sampled if left is None else min(sampled, max(left, FREE_SPREAD.low_s))
    return (left or 0.0) + glance             # away: after the event ends


def fallback_at(av: Availability, now: datetime, pace: float, u: float) -> datetime:
    """When she answers a chat nobody decided on (the agent failed or is off): at the end of
    what she is busy with, or after a glance when she is free. Never lost."""
    if av.level != BusyLevel.FREE and av.ends_at is not None and av.ends_at > now:
        return av.ends_at
    return now + timedelta(seconds=pace * lognormal(FREE_SPREAD, u))


def later_at(hhmm: str, now: datetime, tz: str | None) -> datetime:
    """Her local «HH:MM» as the next such moment (UTC). A time a little in the past means «as
    soon as possible» (now); further back it is tomorrow."""
    local = to_local(now, tz)
    hours, minutes = (int(part) for part in hhmm.split(":"))
    target = local.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    if target <= local:
        if local - target <= PAST_GRACE:
            return now
        target += timedelta(days=1)
    return target.astimezone(UTC)


def local_hhmm(at: datetime, tz: str | None) -> str:
    return to_local(at, tz).strftime("%H:%M")
