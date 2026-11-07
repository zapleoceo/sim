"""Shared UTC date/datetime bounds for analytics and activity queries."""
from datetime import date, datetime, time, timedelta

from fastapi import HTTPException

from sim.clock import to_naive_utc


def parse_bound(value: str, *, end: bool) -> datetime:
    """Date-only end includes that day; datetime end is exclusive."""
    try:
        if len(value) <= 10:
            day = date.fromisoformat(value)
            return datetime.combine(day + timedelta(days=1 if end else 0), time.min)
        return to_naive_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, OverflowError) as exc:
        raise HTTPException(422, "Invalid date bound") from exc


def optional_bounds(start: str | None, end: str | None) -> tuple[datetime | None, datetime | None]:
    lower = parse_bound(start, end=False) if start else None
    upper = parse_bound(end, end=True) if end else None
    if lower is not None and upper is not None and lower >= upper:
        raise HTTPException(422, "'from' must be before 'to'")
    return lower, upper
