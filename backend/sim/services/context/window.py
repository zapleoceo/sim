"""The persona's local calendar days as UTC intervals (docs/memory.md). A day runs from 00:00 to
the next 00:00 of HER time zone, so it is 23 or 25 hours long on daylight-saving changes; a
midnight that does not exist (a zone that jumps over it) starts the day at the jump. Pure."""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from sim.services.context.facts import to_local, zone


def day_start(day: date, tz: str | None) -> datetime:
    """The UTC instant her `day` begins."""
    return datetime.combine(day, time(0), tzinfo=zone(tz)).astimezone(UTC)


def day_bounds(day: date, tz: str | None) -> tuple[datetime, datetime]:
    """[start, end) of her `day` in UTC."""
    return day_start(day, tz), day_start(day + timedelta(days=1), tz)


def local_day(at: datetime, tz: str | None) -> date:
    return to_local(at, tz).date()


def week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def days_between(first: date, last: date) -> list[date]:
    """Every day from `first` to `last`, inclusive (empty when `last` < `first`)."""
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]
