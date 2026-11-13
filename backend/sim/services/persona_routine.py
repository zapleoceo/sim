"""The persona's daily routine: where she is now, was earlier and will be, computed by code.

Asked "where were you yesterday at noon?" twice, a model invents two different answers. The owner
writes the routine once, one entry per line, and code answers from it:

    пн-пт 06:30-07:15 пробежка вдоль Эльбы
    сб,вс 08:00-09:00 долгий завтрак с Андреем
    ежедневно 23:00-06:30 сплю
    2026-11-20 10:00-11:00 стоматолог

Days: пн вт ср чт пт сб вс, a range "пн-пт", a list "сб,вс", "ежедневно", or one date
YYYY-MM-DD (a one-off that replaces the weekly entries it overlaps). An entry past midnight
("23:00-06:30") continues into the next day. Lines that do not parse are ignored (`invalid_lines`
shows them to the owner)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

DAY_CODES = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
EVERY_DAY = ("ежедневно", "каждый день", "*")
LINE = re.compile(r"^\s*(\S+(?:\s+день)?)\s+(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s+(.+?)\s*$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_ROUTINE_LEN = 4000
DAY = 24 * 60


@dataclass(frozen=True)
class Entry:
    days: frozenset[int]      # weekdays 0..6; empty for a dated one-off
    on: date | None           # the one-off's date
    start: int                # minutes from midnight
    end: int                  # minutes; may be <= start (continues past midnight)
    what: str


@dataclass(frozen=True)
class Slot:
    start: int                # minutes from the day's midnight; end may exceed 24h for the next day
    end: int
    what: str

    def label(self) -> str:
        return f"{hhmm(self.start)}-{hhmm(self.end)} {self.what}"


def hhmm(minutes: int) -> str:
    m = minutes % DAY
    return f"{m // 60:02d}:{m % 60:02d}"


def parse_days(spec: str) -> tuple[frozenset[int], date | None] | None:
    spec = spec.strip().lower()
    if spec in EVERY_DAY:
        return frozenset(range(7)), None
    if DATE.match(spec):
        try:
            return frozenset(), date.fromisoformat(spec)
        except ValueError:
            return None
    days: set[int] = set()
    for part in spec.split(","):
        a, _, b = part.partition("-")
        if a not in DAY_CODES or (b and b not in DAY_CODES):
            return None
        i, j = DAY_CODES.index(a), DAY_CODES.index(b or a)
        days.update(range(i, j + 1) if i <= j else [*range(i, 7), *range(0, j + 1)])
    return frozenset(days), None


def parse_line(line: str) -> Entry | None:
    m = LINE.match(line)
    if not m:
        return None
    parsed = parse_days(m.group(1))
    h1, m1, h2, m2 = (int(m.group(k)) for k in (2, 3, 4, 5))
    if parsed is None or h1 > 23 or h2 > 24 or m1 > 59 or m2 > 59:
        return None
    start, end = h1 * 60 + m1, min(h2 * 60 + m2, DAY)
    if start == end:
        return None
    return Entry(parsed[0], parsed[1], start, end, m.group(6))


def meaningful(lines: str) -> list[str]:
    return [ln for ln in (lines or "").splitlines() if ln.strip() and not ln.strip().startswith("#")]


def parse_routine(text: str) -> list[Entry]:
    return [e for e in map(parse_line, meaningful(text)) if e is not None]


def invalid_lines(text: str) -> list[str]:
    return [ln.strip() for ln in meaningful(text) if parse_line(ln) is None]


def starts_on(entry: Entry, day: date) -> bool:
    return day == entry.on if entry.on else day.weekday() in entry.days


def slot(entry: Entry) -> Slot:
    """The entry on its start day; one that runs past midnight ends after 24:00."""
    return Slot(entry.start, entry.end if entry.end > entry.start else entry.end + DAY, entry.what)


def overlaps(a: Slot, b: Slot) -> bool:
    return a.start < b.end and b.start < a.end


def agenda(entries: list[Entry], day: date) -> list[Slot]:
    """What fills `day` (00:00-24:00), sorted: its own entries plus yesterday's past-midnight
    tails. A dated one-off replaces the weekly entries it overlaps."""
    items = [(e.on is not None, slot(e)) for e in entries if starts_on(e, day)]
    items += [
        (e.on is not None, Slot(0, slot(e).end - DAY, e.what))
        for e in entries if starts_on(e, day - timedelta(days=1)) and slot(e).end > DAY
    ]
    dated = [s for one_off, s in items if one_off]
    kept = [s for one_off, s in items if one_off or not any(overlaps(s, d) for d in dated)]
    return sorted(kept, key=lambda s: (s.start, s.end))


def day_line(entries: list[Entry], day: date) -> str:
    items = agenda(entries, day)
    return "; ".join(s.label() for s in items) if items else "ничего по распорядку, свободный день"


def routine_facts(text: str, local: datetime) -> list[str]:
    """Lines for the 'exact data now' block: now, earlier today, later today, yesterday, tomorrow."""
    entries = parse_routine(text)
    if not entries:
        return []
    today, now = local.date(), local.hour * 60 + local.minute
    slots = agenda(entries, today)
    current = [s for s in slots if s.start <= now < s.end]
    done = [s for s in slots if s.end <= now]
    later = [s for s in slots if s.start > now]
    now_text = "; ".join(f"{s.what} (с {hhmm(s.start)} до {hhmm(s.end)})" for s in current) \
        or "по распорядку ничего, свободное время"
    return [
        f"- Где ты сейчас по распорядку: {now_text}",
        f"- Сегодня уже было: {'; '.join(s.label() for s in done) or 'ничего по распорядку'}",
        f"- Дальше сегодня: {'; '.join(s.label() for s in later) or 'ничего по распорядку'}",
        f"- Вчера по распорядку: {day_line(entries, today - timedelta(days=1))}",
        f"- Завтра по распорядку: {day_line(entries, today + timedelta(days=1))}",
    ]


ROUTINE_HEADER = (
    "ТВОЙ РАСПОРЯДОК (где ты была, есть и будешь; отвечай об этом строго по нему и по блоку "
    "\"ТВОИ ТОЧНЫЕ ДАННЫЕ НА СЕЙЧАС\", не придумывай другое; мелкие детали внутри этих дел "
    "можно добавить, но потом не противоречь им):"
)


def routine_block(text: str) -> str:
    """The weekly routine for the stable system prompt (only the lines that parse)."""
    lines = [ln.strip() for ln in meaningful(text) if parse_line(ln) is not None]
    return "\n".join([ROUTINE_HEADER, *lines]) if lines else ""
